"""Run lifecycle management — the public orchestration API used by HTTP routes.

Run lifecycle (see `domain/state_machines.py` for the full table):

    queued → preparing → (planning → waiting_approval →) running ⇄ waiting_approval
          → completed | failed | cancelled | paused | interrupted

* `completed` means the agents finished; the task moves to **Review**, never to Done.
* `paused` / `interrupted` runs keep their sessions and worktree and can be resumed;
  resumption continues the same Claude sessions (`resume=<session id>`).
* Follow-up instructions re-open a finished run and continue the chosen session.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import signal
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from bosun.agents.contracts import ErrorKind, ProviderRuntime, SessionOutcome
from bosun.agents.prompts import single_agent_prompt
from bosun.agents.providers import (
    CLIENT_APP_ENV,
    ProviderConfigError,
    build_provider_runtime,
    check_provider_status,
)
from bosun.db.models import (
    AgentRole,
    AgentRun,
    AgentSession,
    Artifact,
    Subtask,
    Task,
    Workflow,
)
from bosun.domain.enums import (
    ACTIVE_RUN_STATUSES,
    TERMINAL_SUBTASK_STATUSES,
    ExecutionBackend,
    ExecutionMode,
    Provider,
    RunMode,
    RunStatus,
    SessionKind,
    SessionStatus,
    SourceKind,
    SubtaskKind,
    SubtaskStatus,
    TaskStatus,
)
from bosun.events.bus import EventInput
from bosun.ids import utcnow
from bosun.orchestration.config import RunConfig, RunRequest
from bosun.orchestration.context import build_task_context
from bosun.orchestration.git import GitError
from bosun.orchestration.runtime import RunContext, subtask_event
from bosun.orchestration.sessions import SessionRequest, SessionRunner
from bosun.orchestration.team import RunInfo, TeamExecutor, TeamResult
from bosun.settings_store import ProviderSettings, load_settings
from bosun.tasks.service import add_activity, load_task, system_transition
from bosun.workflows.service import (
    WorkflowInvalid,
    agent_changes,
    get_version,
    role_config,
    source_for_run,
)

log = logging.getLogger(__name__)

CONTINUE_PROMPT = (
    "Your previous session was interrupted before you finished. Continue the task from where you left off, "
    "then finish with the report format requested earlier."
)


class RunConflict(Exception):
    """The requested operation conflicts with the run's current state (HTTP 409)."""


class RunNotFound(LookupError):
    pass


DEMO_PROVIDER = ProviderRuntime(
    provider=Provider.SUBSCRIPTION,
    label="Demo backend (no Claude calls)",
    env={},
    expected_api_key_sources=frozenset({"demo"}),
    expected_usage_providers=frozenset({"demo"}),
)


class RunManager:
    def __init__(self, services: Any) -> None:
        self.services = services
        self.sessions = SessionRunner(services)
        self._contexts: dict[str, RunContext] = {}
        self._start_lock = asyncio.Lock()
        self._follow_ups: dict[str, tuple[str, str | None]] = {}
        self._claimed: set[str] = set()

    # ------------------------------------------------------------------ config
    async def resolve_config(self, session: Any, task: Task, request: RunRequest) -> RunConfig:
        settings = await load_settings(session)
        roles = (await session.scalars(select(AgentRole).where(AgentRole.enabled.is_(True)))).all()
        role_configs = {r.key: role_config(r) for r in roles}
        workers = [k for k, r in role_configs.items() if not r.is_coordinator]
        if not workers:
            raise RunConflict(
                "No agent roles are enabled. Enable at least one role in Settings → Agent roles."
            )
        workflow = None
        if request.workflow_id:
            row = await session.get(Workflow, request.workflow_id)
            if row is None or row.archived:
                raise RunNotFound("Workflow not found")
            if (
                request.workflow_version is None
                and request.workflow_revision is not None
                and request.workflow_revision != row.revision
            ):
                raise RunConflict(
                    "This workflow's draft changed after you chose to run it. "
                    "Check the latest draft and run it again."
                )
            try:
                source = await source_for_run(session, row, request.workflow_version)
            except WorkflowInvalid as exc:
                raise RunConflict(str(exc)) from exc
            except LookupError as exc:
                raise RunNotFound(str(exc)) from exc
            workflow = source.snapshot
            # A published version runs with the agents as they were when it was published.
            role_configs = {**role_configs, **source.agents}
            mode = RunMode.WORKFLOW
        else:
            mode = RunMode(
                request.mode
                or ExecutionMode(task.execution_mode or settings.execution.default_mode)
            )
        single_role = request.role or ("implementer" if "implementer" in workers else workers[0])
        if single_role not in role_configs:
            raise RunConflict(f"Role '{single_role}' is not enabled")
        if workflow is not None:
            # The agents the workflow's steps use, in step order; validation checked them.
            team_roles = list(dict.fromkeys(s.agent for s in workflow.steps if s.agent))
        else:
            team_roles = [r for r in (request.team_roles or workers) if r in workers]
        if mode != RunMode.SINGLE and not team_roles:
            raise RunConflict("Select at least one role for the team")
        workspace = task.workspace
        execution = settings.execution
        return RunConfig(
            mode=mode,
            backend=request.backend or execution.backend,
            provider=settings.provider.provider,
            model=request.model or settings.provider.model,
            effort=request.effort or settings.provider.effort,
            single_role=single_role,
            team_roles=team_roles,
            roles=role_configs,
            max_turns=request.max_turns or execution.default_max_turns,
            timeout_minutes=request.timeout_minutes or execution.session_timeout_minutes,
            require_plan_approval=execution.require_plan_approval
            if request.require_plan_approval is None
            else request.require_plan_approval,
            max_team_size=request.max_team_size or execution.max_team_size,
            max_parallel=execution.max_parallel_per_run,
            subtask_max_attempts=execution.subtask_max_attempts,
            allow_subagents=execution.allow_sdk_subagents
            if request.allow_subagents is None
            else request.allow_subagents,
            allow_web_tools=execution.allow_web_tools,
            auto_allow_bash=list(execution.auto_allow_bash),
            test_commands=list(execution.test_commands),
            commit_agent_changes=execution.commit_agent_changes,
            sandbox_bash=execution.sandbox_bash,
            trusted_workspace=bool(workspace and workspace.trusted),
            instructions=request.instructions,
            demo_scenario=request.demo_scenario,
            workflow=workflow,
        )

    async def _provider_runtime(self, config: RunConfig) -> ProviderRuntime:
        if config.backend == ExecutionBackend.DEMO:
            return DEMO_PROVIDER
        settings = await self.services.load_settings()
        prefs = ProviderSettings(
            provider=config.provider,
            model=config.model,
            vertex_project_id=settings.provider.vertex_project_id,
            vertex_region=settings.provider.vertex_region,
        )
        runtime = build_provider_runtime(prefs, self.services.cfg)
        status = await check_provider_status(prefs, self.services.cfg)
        if not status.ready:
            raise ProviderConfigError(f"{runtime.label} is not available: {status.summary}")
        return runtime

    async def preview(self, task_id: str, request: RunRequest) -> dict[str, Any]:
        async with self.services.db.session() as session:
            task = await session.get(Task, task_id)
            if task is None:
                raise RunNotFound("Task not found")
            await session.refresh(task, ["workspace"])
            errors: list[str] = []
            warnings: list[str] = []
            try:
                config = await self.resolve_config(session, task, request)
            except RunConflict as exc:
                return {
                    "can_start": False,
                    "errors": [str(exc)],
                    "warnings": [],
                    "config": None,
                    "scope": {},
                }
            active = await session.scalar(
                select(AgentRun.id).where(
                    AgentRun.task_id == task_id, AgentRun.status.in_(list(ACTIVE_RUN_STATUSES))
                )
            )
            workspace = task.workspace
        if active:
            errors.append("This task already has an active run.")
        ws_info: dict[str, Any] = {}
        if workspace is None:
            errors.append("Assign the task to a workspace before running an agent.")
        else:
            path = Path(workspace.path)
            ws_info = {
                "id": workspace.id,
                "name": workspace.name,
                "path": workspace.path,
                "trusted": workspace.trusted,
            }
            if not path.is_dir():
                errors.append(f"Workspace directory {path} no longer exists.")
            else:
                worktrees = self.services.worktrees
                is_repo = await worktrees.is_repo(path) and await worktrees.has_commits(path)
                ws_info["isolation"] = "worktree" if is_repo else "in_place"
                if is_repo:
                    root = await worktrees.repo_root(path)
                    ws_info["base_branch"] = await worktrees.current_branch(root)
                    if await worktrees.is_dirty(root):
                        warnings.append(
                            "Your checkout has uncommitted changes. Agents work from the last commit in an isolated "
                            "worktree and will not see them."
                        )
                else:
                    warnings.append(
                        "This workspace is not a git repository with commits, so agents will edit files in place "
                        "without isolation, and only one writing run can use it at a time."
                    )
        provider: dict[str, Any] = {"backend": config.backend}
        if config.backend == ExecutionBackend.CLAUDE:
            settings = await self.services.load_settings()
            try:
                status = await check_provider_status(settings.provider, self.services.cfg)
                provider.update(
                    {
                        "provider": status.provider,
                        "ready": status.ready,
                        "summary": status.summary,
                        "details": status.details,
                    }
                )
                if not status.ready:
                    errors.append(status.summary)
            except Exception as exc:  # noqa: BLE001
                errors.append(f"Could not check provider status: {exc}")
        else:
            provider.update(
                {
                    "provider": "demo",
                    "ready": True,
                    "summary": "Demo backend: no Claude calls are made",
                }
            )
        if config.workflow is not None:
            warnings.extend(await self._workflow_warnings(config))
        if config.mode == RunMode.TEAM:
            warnings.append(
                f"A coordinator will plan up to {config.max_team_size} subtasks"
                + (
                    " and wait for your approval before agents start."
                    if config.require_plan_approval
                    else "."
                )
            )
        return {
            "can_start": not errors,
            "errors": errors,
            "warnings": warnings,
            "config": config.model_dump(mode="json", exclude={"roles"}),
            "scope": config.scope(),
            "workspace": ws_info,
            "provider": provider,
        }

    async def _workflow_warnings(self, config: RunConfig) -> list[str]:
        """What a person should know before running this workflow version or draft."""
        workflow = config.workflow
        assert workflow is not None
        warnings = []
        approvals = [s.title for s in workflow.steps if s.is_approval]
        if approvals:
            warnings.append(
                "The run waits for your approval at "
                + ", ".join(approvals)
                + ". It stays waiting, even through restarts, until you decide or cancel it."
            )
        if workflow.version is not None:
            async with self.services.db.session() as session:
                row = await get_version(session, workflow.id, workflow.version)
                changes = await agent_changes(session, row) if row else []
            for c in changes:
                if c.state == "changed":
                    warnings.append(
                        f"Version {workflow.version} runs the {c.name} agent as it was when it "
                        f"was published. Its {', '.join(c.fields)} changed since."
                    )
        return warnings

    # ------------------------------------------------------------------ start
    async def start(
        self, task_id: str, request: RunRequest, idempotency_key: str | None = None
    ) -> AgentRun:
        async with self._start_lock:
            async with self.services.db.session() as session:
                if idempotency_key:
                    existing = await session.scalar(
                        select(AgentRun).where(AgentRun.idempotency_key == idempotency_key)
                    )
                    if existing is not None:
                        return existing
                task = await session.get(Task, task_id)
                if task is None:
                    raise RunNotFound("Task not found")
                await session.refresh(task, ["workspace"])
                if task.archived:
                    raise RunConflict("Archived tasks cannot be run")
                if task.workspace is None:
                    raise RunConflict("Assign the task to a workspace before running an agent")
                if not Path(task.workspace.path).is_dir():
                    raise RunConflict(f"Workspace directory {task.workspace.path} no longer exists")
                active = await session.scalar(
                    select(AgentRun.id).where(
                        AgentRun.task_id == task_id, AgentRun.status.in_(list(ACTIVE_RUN_STATUSES))
                    )
                )
                if active:
                    raise RunConflict("This task already has an active run")
                config = await self.resolve_config(session, task, request)
                number = (
                    await session.scalar(
                        select(func.count(AgentRun.id)).where(AgentRun.task_id == task_id)
                    )
                    or 0
                ) + 1
                run = AgentRun(
                    task_id=task_id,
                    number=number,
                    mode=config.mode,
                    workflow_id=config.workflow.id if config.workflow else None,
                    workflow_version=config.workflow.version if config.workflow else None,
                    status=RunStatus.QUEUED,
                    backend=config.backend,
                    provider="demo" if config.backend == ExecutionBackend.DEMO else config.provider,
                    model=config.model,
                    workspace_id=task.workspace_id,
                    workspace_path=task.workspace.path,
                    config=config.model_dump(mode="json"),
                    idempotency_key=idempotency_key,
                    is_demo=task.is_demo,
                )
                session.add(run)
                try:
                    await session.commit()
                except IntegrityError as exc:
                    await session.rollback()
                    raise RunConflict("This task already has an active run") from exc
                task_key = task.key
                await system_transition(
                    session,
                    task_id,
                    TaskStatus.IN_PROGRESS,
                    f"Run #{number} started (workflow {config.workflow.name}, "
                    + (
                        f"version {config.workflow.version})"
                        if config.workflow.version
                        else "draft)"
                    )
                    if config.workflow
                    else f"Run #{number} started ({config.mode})",
                    run_id=run.id,
                )
                await session.commit()
            await self.services.bus.emit(
                EventInput(
                    run_id=run.id,
                    task_id=task_id,
                    type="run.created",
                    status=RunStatus.QUEUED,
                    data={
                        "number": number,
                        "mode": config.mode,
                        "backend": config.backend,
                        "provider": run.provider,
                        "model": config.model,
                        "task_key": task_key,
                        "task_title": task.title,
                        "single_role": config.single_role,
                        "team_roles": config.team_roles,
                        **(
                            {
                                "workflow": {
                                    "id": config.workflow.id,
                                    "name": config.workflow.name,
                                    "revision": config.workflow.revision,
                                    "version": config.workflow.version,
                                }
                            }
                            if config.workflow
                            else {}
                        ),
                    },
                )
            )
            self.services.bus.notify("task", task_id)
            self._launch(run.id, task_id, task_key, "start")
            return run

    def _launch(
        self, run_id: str, task_id: str, task_key: str, entry: str, **kwargs: Any
    ) -> RunContext:
        ctx = RunContext(run_id=run_id, task_id=task_id, task_key=task_key)
        self._contexts[run_id] = ctx
        ctx.task = asyncio.create_task(self._drive(ctx, entry, **kwargs), name=f"run:{run_id}")
        return ctx

    def is_active(self, run_id: str) -> bool:
        return run_id in self._contexts

    # ------------------------------------------------------------------ driving
    async def _drive(
        self,
        ctx: RunContext,
        entry: str,
        message: str | None = None,
        target_session_id: str | None = None,
    ) -> None:
        try:
            await self._drive_inner(ctx, entry, message, target_session_id)
        except asyncio.CancelledError:
            await self.services.store.set_run_status(
                ctx.run_id,
                RunStatus.INTERRUPTED,
                reason="Bosun shut down while this run was active",
                force=True,
            )
            raise
        except Exception as exc:  # noqa: BLE001
            log.exception("Run %s crashed", ctx.run_id)
            try:
                await self._finish(
                    ctx,
                    None,
                    TeamResult(
                        "failed", error=f"Internal error: {exc}", error_kind=ErrorKind.UNKNOWN
                    ),
                )
            except Exception:  # noqa: BLE001 - never leave a run looking active
                log.exception("Could not finish crashed run %s", ctx.run_id)
                with contextlib.suppress(Exception):
                    await self.services.store.set_run_status(
                        ctx.run_id,
                        RunStatus.FAILED,
                        reason=f"Internal error: {exc}",
                        force=True,
                        error=f"Internal error: {exc}",
                        error_kind=ErrorKind.UNKNOWN,
                    )
        finally:
            self._contexts.pop(ctx.run_id, None)
            follow_up = self._follow_ups.pop(ctx.run_id, None)
            if follow_up is None and ctx.undelivered and ctx.stop_reason is None:
                # Messages that arrived as the agent finished become a follow-up turn.
                follow_up = (
                    "\n\n".join(text for _, text in ctx.undelivered),
                    ctx.undelivered[-1][0],
                )
                ctx.undelivered.clear()
            delivered = False
            if follow_up is not None:
                try:
                    await self.send_message(ctx.run_id, follow_up[0], follow_up[1])
                    delivered = True
                except Exception:  # noqa: BLE001 - reported below as undelivered
                    log.warning("Could not deliver follow-up for run %s", ctx.run_id, exc_info=True)
                    ctx.undelivered.append((follow_up[1] or "", follow_up[0]))
            if not delivered:
                await self._report_undelivered(ctx)

    async def _report_undelivered(self, ctx: RunContext) -> None:
        for session_id, text in ctx.undelivered:
            with contextlib.suppress(Exception):
                await self.services.bus.emit(
                    EventInput(
                        run_id=ctx.run_id,
                        task_id=ctx.task_id,
                        session_id=session_id or None,
                        agent_id=session_id or None,
                        type="user.message",
                        data={"text": text, "undelivered": True},
                    )
                )
        ctx.undelivered.clear()

    async def _drive_inner(
        self, ctx: RunContext, entry: str, message: str | None, target_session_id: str | None
    ) -> None:
        async with self.services.db.session() as session:
            run = await session.get(AgentRun, ctx.run_id)
            assert run is not None
            config = RunConfig.model_validate(run.config)
        ctx.remembered = list(config.remembered_approvals)
        await self.services.store.set_run_status(ctx.run_id, RunStatus.PREPARING, reason=None)

        try:
            provider = await self._provider_runtime(config)
        except ProviderConfigError as exc:
            await self._finish(
                ctx, None, TeamResult("failed", error=str(exc), error_kind=ErrorKind.AUTH_REQUIRED)
            )
            return

        try:
            info = await self._prepare_workspace(ctx, run, config)
        except (GitError, OSError, RunConflict) as exc:
            await self._finish(
                ctx,
                None,
                TeamResult(
                    "failed", error=f"Workspace error: {exc}", error_kind=ErrorKind.WORKSPACE
                ),
            )
            return

        lock = (
            self.services.worktrees.lock_for(f"in-place:{info.worktree_root}")
            if info.isolation == "in_place"
            else contextlib.nullcontext()
        )
        async with lock:
            if entry == "follow_up":
                result = await self._follow_up(
                    ctx, config, provider, info, message or "", target_session_id
                )
            elif config.mode == RunMode.SINGLE:
                result = await self._single(ctx, config, provider, info, entry)
            elif config.mode == RunMode.WORKFLOW:
                result = await self._workflow(ctx, config, provider, info)
            else:
                result = await self._team(ctx, config, provider, info)
        await self._finish(ctx, info, result)

    async def _prepare_workspace(
        self, ctx: RunContext, run: AgentRun, config: RunConfig
    ) -> RunInfo:
        worktrees = self.services.worktrees
        async with self.services.db.session() as session:
            task = await session.get(Task, ctx.task_id)
            assert task is not None
            title = task.title
        ctx.source_root = Path(run.workspace_path) if run.workspace_path else None
        if run.worktree_path:
            path = Path(run.worktree_path)
            if not path.is_dir():
                raise RunConflict(f"The run's working directory {path} no longer exists")
            root = await worktrees.repo_root(path) if run.isolation == "worktree" else path
            return RunInfo(
                ctx.run_id, ctx.task_id, ctx.task_key, title, path, root, run.branch, run.isolation
            )
        workspace = Path(run.workspace_path or "")
        if not workspace.is_dir():
            raise RunConflict(f"Workspace directory {workspace} no longer exists")
        prepared = await worktrees.prepare_run(
            workspace,
            ctx.run_id,
            ctx.task_key,
            title,
            pull_request=await self._pull_request_source(ctx.task_id),
        )
        root = (
            await worktrees.repo_root(prepared.path)
            if prepared.isolation == "worktree"
            else prepared.path
        )
        await self.services.store.update_run(
            ctx.run_id,
            worktree_path=str(prepared.path),
            branch=prepared.branch,
            base_branch=prepared.base_branch,
            base_commit=prepared.base_commit,
            isolation=prepared.isolation,
        )
        await self.services.bus.emit(
            EventInput(
                run_id=ctx.run_id,
                task_id=ctx.task_id,
                type="workspace.prepared",
                status="completed",
                data={
                    "isolation": prepared.isolation,
                    "path": str(prepared.path),
                    "branch": prepared.branch,
                    "base_branch": prepared.base_branch,
                    "base_commit": prepared.base_commit,
                    "uncommitted_source_changes": prepared.dirty_source,
                    "base": prepared.base_label,
                    "notice": prepared.notice,
                },
            )
        )
        return RunInfo(
            ctx.run_id,
            ctx.task_id,
            ctx.task_key,
            title,
            prepared.path,
            root,
            prepared.branch,
            prepared.isolation,
        )

    async def _pull_request_source(self, task_id: str) -> tuple[str, int] | None:
        async with self.services.db.session() as session:
            task = await load_task(session, task_id)
        for source in task.sources if task else []:
            if source.kind == SourceKind.GITHUB_PR and source.repo and source.number:
                return source.repo, source.number
        return None

    async def _task_context(self, ctx: RunContext, config: RunConfig):  # noqa: ANN202
        async with self.services.db.session() as session:
            return await build_task_context(session, ctx.task_id, config.instructions)

    async def _single(
        self,
        ctx: RunContext,
        config: RunConfig,
        provider: ProviderRuntime,
        info: RunInfo,
        entry: str,
    ) -> TeamResult:
        task_ctx = await self._task_context(ctx, config)
        role = config.roles.get(config.single_role)
        if role is None:
            return TeamResult(
                "failed",
                error=f"Role {config.single_role} is no longer available",
                error_kind=ErrorKind.INVALID_REQUEST,
            )
        previous = await self._latest_session(ctx.run_id, kind=SessionKind.AGENT)
        resume = None
        prompt = single_agent_prompt(task_ctx)
        if (
            entry in ("resume", "retry")
            and previous
            and previous.sdk_session_id
            and previous.cwd
            and Path(previous.cwd).is_dir()
        ):
            resume = previous
            prompt = (
                CONTINUE_PROMPT
                if entry == "resume"
                else (
                    f"The previous attempt ended with an error: {previous.error or 'unknown error'}. "
                    "Review the state of your working directory and try again, then finish with the requested report."
                )
            )
        ctx.active_status = RunStatus.RUNNING
        await self.services.store.set_run_status(
            ctx.run_id, RunStatus.RUNNING, reason=None, strategy="single"
        )
        _, outcome = await self.sessions.run(
            SessionRequest(
                ctx=ctx,
                config=config,
                role=role,
                prompt=prompt,
                cwd=info.cwd,
                worktree_root=info.worktree_root,
                branch=info.branch,
                isolated=info.isolation == "worktree",
                resume=resume,
                attempt=(previous.attempt + 1)
                if previous and resume is None and entry == "retry"
                else 1,
                allow_subagents=config.allow_subagents,
                scenario={"task_title": info.task_title},
            ),
            provider,
        )
        return self._result_from_outcome(ctx, outcome)

    def _result_from_outcome(self, ctx: RunContext, outcome: SessionOutcome) -> TeamResult:
        if outcome.status == SessionStatus.COMPLETED:
            return TeamResult("completed", summary=outcome.result)
        if (
            outcome.status in (SessionStatus.INTERRUPTED, SessionStatus.CANCELLED)
            or ctx.stop_event.is_set()
        ):
            return TeamResult("stopped")
        return TeamResult(
            "failed", error=outcome.error or "The agent failed", error_kind=outcome.error_kind
        )

    async def _team(
        self, ctx: RunContext, config: RunConfig, provider: ProviderRuntime, info: RunInfo
    ) -> TeamResult:
        task_ctx = await self._task_context(ctx, config)
        team = TeamExecutor(self, ctx, info, config, provider, task_ctx)
        plan, error = await team.plan()
        if error is not None:
            return error
        assert plan is not None
        async with self.services.db.session() as session:
            run = await session.get(AgentRun, ctx.run_id)
            approved = bool(run and run.config.get("plan_approved"))
        needs_approval = (
            plan.get("strategy") == "team" and config.require_plan_approval and not approved
        )
        if needs_approval and not await team.approve_plan(plan):
            if ctx.stop_event.is_set():
                return TeamResult("stopped")
            return TeamResult("rejected", error="The plan was rejected")
        async with self.services.db.session() as session:
            run = await session.get(AgentRun, ctx.run_id)
            assert run is not None
            run.config = {**run.config, "plan_approved": True}
            await session.commit()
        await team.schedule()
        return await team.aggregate(plan.get("strategy", "team"))

    async def _workflow(
        self, ctx: RunContext, config: RunConfig, provider: ProviderRuntime, info: RunInfo
    ) -> TeamResult:
        """An authored workflow: the team engine with a fixed plan and no coordinator."""
        task_ctx = await self._task_context(ctx, config)
        team = TeamExecutor(self, ctx, info, config, provider, task_ctx)
        await team.plan_workflow()
        await team.schedule()
        return await team.finish_workflow()

    async def _follow_up(
        self,
        ctx: RunContext,
        config: RunConfig,
        provider: ProviderRuntime,
        info: RunInfo,
        message: str,
        target_session_id: str | None,
    ) -> TeamResult:
        async with self.services.db.session() as session:
            target = (
                await session.get(AgentSession, target_session_id) if target_session_id else None
            )
            subtask = (
                await session.get(Subtask, target.subtask_id)
                if target and target.subtask_id
                else None
            )
        if target is None or not target.sdk_session_id:
            return TeamResult(
                "failed",
                error="That agent session cannot be continued",
                error_kind=ErrorKind.INVALID_REQUEST,
            )
        cwd = Path(target.cwd or info.cwd)
        if not cwd.is_dir():
            return TeamResult(
                "failed",
                error=f"The agent's working directory {cwd} no longer exists",
                error_kind=ErrorKind.WORKSPACE,
            )
        role = config.roles.get(target.role) or config.roles.get(config.single_role)
        assert role is not None
        root = (
            await self.services.worktrees.repo_root(cwd)
            if info.isolation == "worktree"
            else info.worktree_root
        )
        ctx.active_status = RunStatus.RUNNING
        await self.services.store.set_run_status(ctx.run_id, RunStatus.RUNNING, reason=None)
        _, outcome = await self.sessions.run(
            SessionRequest(
                ctx=ctx,
                config=config,
                role=role,
                prompt=message,
                cwd=cwd,
                worktree_root=root,
                branch=target.branch or info.branch,
                isolated=info.isolation == "worktree",
                kind=SessionKind(target.kind),
                resume=target,
                subtask_id=target.subtask_id,
                parent_session_id=target.parent_session_id,
                scenario={"task_title": info.task_title},
            ),
            provider,
        )
        result = self._result_from_outcome(ctx, outcome)
        if (
            result.status == "completed"
            and subtask
            and subtask.branch
            and subtask.worktree_path
            and info.branch
        ):
            worktrees = self.services.worktrees
            async with ctx.merge_lock:
                commit = await worktrees.commit_all(cwd, f"{ctx.task_key} {subtask.key}: follow-up")
                if commit:
                    ok, conflicts, _ = await worktrees.merge_into(
                        info.cwd, subtask.branch, f"Merge follow-up for {subtask.key}"
                    )
                    if not ok:
                        return TeamResult(
                            "failed",
                            error=f"Follow-up changes conflict with the run branch: {', '.join(conflicts)}",
                            error_kind=ErrorKind.WORKSPACE,
                        )
        if result.status == "completed":
            async with self.services.db.session() as session:
                run = await session.get(AgentRun, ctx.run_id)
                previous = run.summary if run else None
            result.summary = (
                f"{previous}\n\n---\n\n**Follow-up:** {message}\n\n{outcome.result or ''}"
                if previous
                else outcome.result
            )
        return result

    async def _latest_session(
        self, run_id: str, kind: SessionKind | None = None
    ) -> AgentSession | None:
        async with self.services.db.session() as session:
            stmt = select(AgentSession).where(AgentSession.run_id == run_id)
            if kind is not None:
                stmt = stmt.where(AgentSession.kind == kind)
            return await session.scalar(stmt.order_by(AgentSession.created_at.desc()))

    # ------------------------------------------------------------------ finishing
    async def _finish(self, ctx: RunContext, info: RunInfo | None, result: TeamResult) -> None:
        store = self.services.store
        async with self.services.db.session() as session:
            run = await session.get(AgentRun, ctx.run_id)
            assert run is not None
            config = RunConfig.model_validate(run.config)
            number = run.number

        commit = None
        if (
            info is not None
            and info.isolation == "worktree"
            and config.commit_agent_changes
            # A rejected plan made no changes; a rejected workflow approval may follow some.
            and (result.status != "rejected" or config.mode == RunMode.WORKFLOW)
        ):
            try:
                commit = await self.services.worktrees.commit_all(
                    info.cwd, f"{ctx.task_key}: {info.task_title[:60]} (run #{number})"
                )
            except GitError as exc:
                log.warning("Could not commit agent changes for %s: %s", ctx.run_id, exc)
            if commit:
                await self.services.bus.emit(
                    EventInput(
                        run_id=ctx.run_id,
                        task_id=ctx.task_id,
                        type="artifact.commit",
                        status="completed",
                        data={"commit": commit, "branch": info.branch},
                    )
                )
        usage, cost = await self._aggregate_usage(ctx.run_id, config)
        diff_stats = await self._record_diff(ctx, info)

        stopped = result.status == "stopped" or ctx.stop_event.is_set()
        async with self.services.db.session() as session:
            if stopped and ctx.stop_reason == "pause":
                status, reason = RunStatus.PAUSED, "Paused by the operator"
            elif stopped and ctx.stop_reason == "shutdown":
                status, reason = (
                    RunStatus.INTERRUPTED,
                    "Bosun shut down while this run was active",
                )
            elif stopped:
                status, reason = RunStatus.CANCELLED, "Cancelled by the operator"
            elif result.status == "rejected":
                status, reason = RunStatus.CANCELLED, result.error
            elif result.status == "completed":
                status, reason = RunStatus.COMPLETED, None
            else:
                status, reason = RunStatus.FAILED, result.error

            if status == RunStatus.COMPLETED:
                await system_transition(
                    session,
                    ctx.task_id,
                    TaskStatus.REVIEW,
                    f"Run #{number} finished — ready for review",
                    run_id=ctx.run_id,
                )
            elif status == RunStatus.FAILED:
                await system_transition(
                    session,
                    ctx.task_id,
                    TaskStatus.BLOCKED,
                    f"Run #{number} failed: {(result.error or '')[:200]}",
                    run_id=ctx.run_id,
                )
            elif status == RunStatus.CANCELLED:
                await system_transition(
                    session,
                    ctx.task_id,
                    TaskStatus.READY,
                    f"Run #{number} cancelled: {reason}",
                    run_id=ctx.run_id,
                )
            else:
                task = await session.get(Task, ctx.task_id)
                if task is not None:
                    await add_activity(
                        session,
                        task,
                        "run",
                        f"Run #{number} {status}: {reason}",
                        actor="system",
                        run_id=ctx.run_id,
                    )
            await session.commit()

        fields: dict[str, Any] = {
            "usage": {**usage, **({"diff": diff_stats} if diff_stats else {})},
            "cost_usd": cost,
        }
        if status == RunStatus.COMPLETED:
            fields.update(summary=result.summary, error=None, error_kind=None)
        elif status == RunStatus.FAILED:
            fields.update(error=result.error, error_kind=result.error_kind)
        await store.set_run_status(ctx.run_id, status, reason=reason, force=True, **fields)
        await self.services.bus.emit(
            EventInput(
                run_id=ctx.run_id,
                task_id=ctx.task_id,
                type=f"run.{status.value}",
                status=status,
                data={
                    "summary": (result.summary or "")[:6000],
                    "error": result.error,
                    "error_kind": result.error_kind,
                    "reason": reason,
                    "usage": usage,
                    "diff": diff_stats,
                },
            )
        )
        self.services.bus.notify("task", ctx.task_id)

    async def _aggregate_usage(
        self, run_id: str, config: RunConfig
    ) -> tuple[dict[str, Any], float | None]:
        async with self.services.db.session() as session:
            rows = (
                await session.scalars(select(AgentSession).where(AgentSession.run_id == run_id))
            ).all()
        usage: dict[str, Any] = {"sessions": len(rows), "num_turns": sum(r.num_turns for r in rows)}
        for row in rows:
            for key, value in (row.usage or {}).items():
                if isinstance(value, int):
                    usage[key] = usage.get(key, 0) + value
        # Per-request monetary cost is only meaningful for API-billed providers.
        cost = None
        if config.backend == ExecutionBackend.CLAUDE and config.provider in (
            Provider.API_KEY,
            Provider.VERTEX,
        ):
            costs = [r.cost_usd for r in rows if r.cost_usd is not None]
            cost = round(sum(costs), 4) if costs else None
        return usage, cost

    async def _record_diff(self, ctx: RunContext, info: RunInfo | None) -> dict[str, Any] | None:
        if info is None or info.isolation != "worktree":
            return None
        async with self.services.db.session() as session:
            run = await session.get(AgentRun, ctx.run_id)
            base = run.base_commit if run else None
        if not base:
            return None
        try:
            diff = await self.services.worktrees.diff(info.cwd, base)
        except GitError:
            return None
        stats = {
            "files": len(diff["files"]),  # type: ignore[arg-type]
            "additions": diff["additions"],
            "deletions": diff["deletions"],
            "commits": len(diff["commits"]),  # type: ignore[arg-type]
        }
        async with self.services.db.session() as session:
            session.add(
                Artifact(
                    run_id=ctx.run_id,
                    kind="diff",
                    title=f"{stats['files']} files changed",
                    data=stats,
                )
            )
            await session.commit()
        return stats

    # ------------------------------------------------------------------ controls
    async def _run(self, run_id: str) -> AgentRun:
        async with self.services.db.session() as session:
            run = await session.get(AgentRun, run_id)
        if run is None:
            raise RunNotFound("Run not found")
        return run

    async def _task_key(self, task_id: str) -> str:
        async with self.services.db.session() as session:
            task = await session.get(Task, task_id)
            return task.key if task else task_id

    async def cancel(self, run_id: str) -> None:
        self._follow_ups.pop(run_id, None)
        ctx = self._contexts.get(run_id)
        if ctx is not None:
            ctx.request_stop("cancel")
            return
        run = await self._run(run_id)
        if run.status in (
            RunStatus.PAUSED,
            RunStatus.INTERRUPTED,
            RunStatus.FAILED,
            RunStatus.QUEUED,
        ):
            async with self.services.db.session() as session:
                await system_transition(
                    session,
                    run.task_id,
                    TaskStatus.READY,
                    f"Run #{run.number} cancelled",
                    run_id=run_id,
                )
                await session.commit()
            await self.services.approvals.cancel_steps(
                run_id, "The run was cancelled before anyone decided"
            )
            await self.services.store.set_run_status(
                run_id, RunStatus.CANCELLED, reason="Cancelled by the operator", force=True
            )
            return
        raise RunConflict(f"Run is {run.status}; nothing to cancel")

    async def pause(self, run_id: str) -> None:
        ctx = self._contexts.get(run_id)
        if ctx is None:
            raise RunConflict("Run is not active")
        ctx.request_stop("pause")

    async def _relaunch(self, run: AgentRun, entry: str, **kwargs: Any) -> None:
        async with self.services.db.session() as session:
            active = await session.scalar(
                select(AgentRun.id).where(
                    AgentRun.task_id == run.task_id,
                    AgentRun.status.in_(list(ACTIVE_RUN_STATUSES)),
                    AgentRun.id != run.id,
                )
            )
        if active:
            raise RunConflict("Another run for this task is active")
        await self.services.store.set_run_status(run.id, RunStatus.QUEUED, reason=None)
        async with self.services.db.session() as session:
            await system_transition(
                session,
                run.task_id,
                TaskStatus.IN_PROGRESS,
                f"Run #{run.number} {entry.replace('_', ' ')}",
                run_id=run.id,
            )
            await session.commit()
        self._launch(run.id, run.task_id, await self._task_key(run.task_id), entry, **kwargs)

    @contextlib.contextmanager
    def _claim(self, run_id: str) -> Iterator[None]:
        """Serialise control actions on an inactive run (no awaits between check and claim)."""
        if run_id in self._contexts or run_id in self._claimed:
            raise RunConflict("The run is active or another action on it is in progress")
        self._claimed.add(run_id)
        try:
            yield
        finally:
            self._claimed.discard(run_id)

    async def resume(self, run_id: str) -> None:
        with self._claim(run_id):
            await self._resume(run_id)

    async def retry(self, run_id: str) -> None:
        with self._claim(run_id):
            await self._retry(run_id)

    async def update_subtask(self, run_id: str, subtask_id: str, action: str) -> None:
        with self._claim(run_id):
            await self._update_subtask(run_id, subtask_id, action)

    async def integrate(self, run_id: str, action: str) -> dict[str, Any]:
        with self._claim(run_id):
            return await self._integrate(run_id, action)

    async def send_message(
        self, run_id: str, text: str, session_id: str | None = None, *, interrupt: bool = False
    ) -> str:
        if run_id in self._contexts:
            return await self._send_message(run_id, text, session_id, interrupt=interrupt)
        with self._claim(run_id):
            return await self._send_message(run_id, text, session_id, interrupt=interrupt)

    async def _unfinished_subtasks(self, run_id: str) -> int:
        async with self.services.db.session() as session:
            return (
                await session.scalar(
                    select(func.count(Subtask.id)).where(
                        Subtask.run_id == run_id,
                        Subtask.status.not_in(list(TERMINAL_SUBTASK_STATUSES)),
                    )
                )
            ) or 0

    async def _resume(self, run_id: str) -> None:
        run = await self._run(run_id)
        if run.status not in (RunStatus.PAUSED, RunStatus.INTERRUPTED):
            raise RunConflict(
                f"Only paused or interrupted runs can be resumed (run is {run.status})"
            )
        await self._relaunch(run, "resume")

    async def _retry(self, run_id: str) -> None:
        run = await self._run(run_id)
        if run.status not in (RunStatus.FAILED, RunStatus.INTERRUPTED):
            raise RunConflict(
                f"Only failed or interrupted runs can be retried (run is {run.status})"
            )
        async with self.services.db.session() as session:
            for sub in (
                await session.scalars(select(Subtask).where(Subtask.run_id == run_id))
            ).all():
                if sub.status in (
                    SubtaskStatus.FAILED,
                    SubtaskStatus.CANCELLED,
                    SubtaskStatus.CONFLICT,
                ):
                    sub.status = SubtaskStatus.PENDING
                    sub.attempts = 0
                    sub.error = None
            await session.commit()
        await self._relaunch(run, "retry")

    async def _update_subtask(self, run_id: str, subtask_id: str, action: str) -> None:
        run = await self._run(run_id)
        if run.status not in (RunStatus.FAILED, RunStatus.PAUSED, RunStatus.INTERRUPTED):
            raise RunConflict(
                f"Subtasks can be changed when the run is failed, paused or interrupted (run is {run.status})"
            )
        async with self.services.db.session() as session:
            sub = await session.get(Subtask, subtask_id)
            if sub is None or sub.run_id != run_id:
                raise RunNotFound("Subtask not found")
            approval = sub.kind == SubtaskKind.APPROVAL
            if approval and sub.status == SubtaskStatus.WAITING_APPROVAL:
                raise RunConflict("This step is waiting for your decision: approve or reject it")
            if action == "retry":
                if approval and sub.status == SubtaskStatus.COMPLETED:
                    raise RunConflict("This step was already approved")
                # For an approval step, asks again when the run resumes.
                sub.status, sub.attempts, sub.error = SubtaskStatus.PENDING, 0, None
            elif action == "skip":
                if sub.status == SubtaskStatus.COMPLETED:
                    raise RunConflict("Completed subtasks cannot be skipped")
                if approval:
                    raise RunConflict(
                        "Approval steps can't be skipped: retry it to ask again, or cancel the run"
                    )
                sub.status, sub.error = SubtaskStatus.SKIPPED, "Skipped by the operator"
            else:
                raise RunConflict(f"Unknown subtask action {action}")
            await session.commit()
        await self.services.bus.emit(
            EventInput(
                run_id=run_id,
                task_id=run.task_id,
                type="subtask.status",
                status=sub.status,
                agent_id=f"subtask:{subtask_id}",
                data={
                    "subtask_id": subtask_id,
                    "key": sub.key,
                    "title": sub.title,
                    "role": sub.role,
                    "to": sub.status,
                    "by": "operator",
                },
            )
        )
        await self._relaunch(run, "resume")

    async def _send_message(
        self, run_id: str, text: str, session_id: str | None = None, *, interrupt: bool = False
    ) -> str:
        text = text.strip()
        if not text:
            raise RunConflict("Message is empty")
        ctx = self._contexts.get(run_id)
        if ctx is not None:
            live = ctx.sessions.get(session_id) if session_id else None
            if live is None and session_id is None:
                candidates = [
                    s for s in ctx.sessions.values() if s.role not in ("coordinator",)
                ] or list(ctx.sessions.values())
                live = candidates[-1] if candidates else None
            if live is None:
                raise RunConflict("No running agent can receive messages right now")
            if interrupt:
                if await self._unfinished_subtasks(run_id):
                    raise RunConflict(
                        "Interrupting is not available while team subtasks are running; "
                        "send without interrupting, or pause the run."
                    )
                self._follow_ups[run_id] = (text, live.session_id)
                ctx.request_stop("pause")
                return "interrupting"
            await live.inbox.put(text)
            return "queued"

        run = await self._run(run_id)
        if run.status in (RunStatus.CANCELLED,):
            raise RunConflict("This run was cancelled; start a new run instead")
        if run.status not in (
            RunStatus.COMPLETED,
            RunStatus.FAILED,
            RunStatus.PAUSED,
            RunStatus.INTERRUPTED,
        ):
            raise RunConflict(f"Run is {run.status}")
        if run.status in (
            RunStatus.PAUSED,
            RunStatus.INTERRUPTED,
        ) and await self._unfinished_subtasks(run_id):
            raise RunConflict(
                "Resume the run to finish its remaining subtasks before sending follow-ups."
            )
        target = None
        async with self.services.db.session() as session:
            if session_id:
                target = await session.get(AgentSession, session_id)
                if target is None or target.run_id != run_id:
                    raise RunNotFound("Session not found")
            else:
                rows = (
                    await session.scalars(
                        select(AgentSession)
                        .where(AgentSession.run_id == run_id)
                        .order_by(AgentSession.created_at.desc())
                    )
                ).all()
                config = RunConfig.model_validate(run.config)
                writers = [
                    r
                    for r in rows
                    if r.kind == SessionKind.AGENT
                    and config.roles.get(r.role)
                    and config.roles[r.role].permission_profile != "read_only"
                ]
                target = (writers or rows or [None])[0]
        if target is None or not target.sdk_session_id:
            raise RunConflict("This run has no agent session that can be continued")
        await self.services.bus.emit(
            EventInput(
                run_id=run_id,
                task_id=run.task_id,
                session_id=target.id,
                agent_id=target.id,
                type="user.message",
                data={"text": text, "follow_up": True},
            )
        )
        await self._relaunch(run, "follow_up", message=text, target_session_id=target.id)
        return "started"

    async def _integrate(self, run_id: str, action: str) -> dict[str, Any]:
        run = await self._run(run_id)
        if run.isolation != "worktree" or not run.branch:
            raise RunConflict("This run edited files in place; there is nothing to integrate")
        if run.integration_status in ("merged", "discarded"):
            raise RunConflict(f"Run changes were already {run.integration_status}")
        worktrees = self.services.worktrees
        async with self.services.db.session() as session:
            task = await session.get(Task, run.task_id)
            assert task is not None
            subtasks = (
                await session.scalars(select(Subtask).where(Subtask.run_id == run_id))
            ).all()
        result: dict[str, Any] = {"action": action}
        if action == "merge":
            if run.status not in (
                RunStatus.COMPLETED,
                RunStatus.FAILED,
                RunStatus.PAUSED,
                RunStatus.INTERRUPTED,
                RunStatus.CANCELLED,
            ):
                raise RunConflict(f"Run is {run.status}")
            try:
                await self._commit_leftovers(run, task.key)
                commit = await worktrees.integrate(
                    Path(run.workspace_path or ""),
                    run.branch,
                    run.base_branch,
                    run.base_commit,
                    f"Merge {task.key}: {task.title[:60]}",
                )
            except GitError as exc:
                raise RunConflict(str(exc)) from exc
            result["commit"] = commit
            status, message = (
                "merged",
                f"Agent changes merged into {run.base_branch or 'your branch'} ({commit[:8]})",
            )
        elif action == "discard":
            for sub in subtasks:
                if sub.worktree_path:
                    await worktrees.remove(Path(sub.worktree_path), sub.branch)
            if run.worktree_path:
                root = (
                    await worktrees.repo_root(Path(run.worktree_path))
                    if Path(run.worktree_path).exists()
                    else Path(run.worktree_path)
                )
                await worktrees.remove(root, run.branch)
            status, message = "discarded", "Agent worktree and branch discarded"
        else:
            raise RunConflict(f"Unknown action {action}")
        await self.services.store.update_run(run_id, integration_status=status)
        async with self.services.db.session() as session:
            task = await session.get(Task, run.task_id)
            assert task is not None
            await add_activity(session, task, "integration", message, actor="human", run_id=run_id)
            await session.commit()
        await self.services.bus.emit(
            EventInput(
                run_id=run_id, task_id=run.task_id, type=f"run.{status}", status=status, data=result
            )
        )
        self.services.bus.notify("task", run.task_id)
        return result

    async def _commit_leftovers(self, run: AgentRun, task_key: str) -> None:
        """Make sure nothing the agents left uncommitted is silently left out of a merge."""
        if not run.worktree_path or not Path(run.worktree_path).is_dir():
            raise GitError("The run's worktree no longer exists")
        worktrees = self.services.worktrees
        config = RunConfig.model_validate(run.config)
        if not config.commit_agent_changes:
            root = await worktrees.repo_root(Path(run.worktree_path))
            if await worktrees.is_dirty(root, untracked=True):
                raise GitError(
                    f"The run worktree {root} has uncommitted changes and automatic commits are "
                    "off. Commit or discard them there first."
                )
            return
        commit = await worktrees.commit_all(
            Path(run.worktree_path), f"{task_key}: remaining agent changes (run #{run.number})"
        )
        if commit:
            await self.services.bus.emit(
                EventInput(
                    run_id=run.id,
                    task_id=run.task_id,
                    type="artifact.commit",
                    status="completed",
                    data={"commit": commit, "branch": run.branch},
                )
            )

    async def diff(self, run_id: str) -> dict[str, Any]:
        run = await self._run(run_id)
        if run.isolation != "worktree" or not run.worktree_path or not run.base_commit:
            return {
                "available": False,
                "reason": "This run did not use an isolated git worktree",
                "files": [],
                "patch": "",
            }
        path = Path(run.worktree_path)
        if not path.is_dir():
            return {
                "available": False,
                "reason": f"Run worktree was {run.integration_status or 'removed'}",
                "files": [],
                "patch": "",
            }
        diff = await self.services.worktrees.diff(path, run.base_commit)
        return {
            "available": True,
            "branch": run.branch,
            "base_branch": run.base_branch,
            "base_commit": run.base_commit,
            **diff,
        }

    # ------------------------------------------------------------------ recovery
    async def recover(self) -> dict[str, int]:
        """Reconcile persisted state with reality after a (re)start.

        No agent process survives a restart of this application, so runs that
        the database lists as active are marked interrupted; nothing is assumed
        to still be running.
        """
        counts = {"runs": 0, "sessions": 0, "processes_killed": 0}
        reason = "Bosun restarted while this run was active. Resume it to continue."
        async with self.services.db.session() as session:
            runs = (
                await session.scalars(
                    select(AgentRun).where(AgentRun.status.in_(list(ACTIVE_RUN_STATUSES)))
                )
            ).all()
            sessions = (
                await session.scalars(
                    select(AgentSession).where(
                        AgentSession.status.in_(
                            [
                                SessionStatus.PENDING,
                                SessionStatus.RUNNING,
                                SessionStatus.WAITING_APPROVAL,
                            ]
                        )
                    )
                )
            ).all()
            for row in sessions:
                if row.pid and _kill_if_claude(row.pid):
                    counts["processes_killed"] += 1
                row.status = SessionStatus.INTERRUPTED
                row.error = "Interrupted by a Bosun restart"
                row.pid = None
                counts["sessions"] += 1
            reset: list[tuple[str, Subtask, str]] = []
            for sub in (
                await session.scalars(
                    select(Subtask).where(
                        Subtask.status.in_([SubtaskStatus.RUNNING, SubtaskStatus.WAITING_APPROVAL])
                    )
                )
            ).all():
                # A workflow approval step keeps waiting: its request outlives the restart.
                if sub.kind == SubtaskKind.APPROVAL:
                    continue
                reset.append((sub.status, sub, sub.run_id))
                sub.status = SubtaskStatus.PENDING
                sub.error = "Interrupted by a Bosun restart"
            task_of = {r.id: r.task_id for r in runs}
            run_ids = [(r.id, r.task_id, r.number) for r in runs]
            for run in runs:
                run.status = RunStatus.INTERRUPTED
                run.status_reason = reason
                run.ended_at = utcnow()
            await session.commit()
            for run_id, task_id, number in run_ids:
                task = await session.get(Task, task_id)
                if task is not None:
                    await add_activity(
                        session,
                        task,
                        "run",
                        f"Run #{number} interrupted by a restart",
                        actor="system",
                        run_id=run_id,
                    )
            await session.commit()
        # The live graph is built from events, so it hears about the steps put back too.
        for previous, sub, run_id in reset:
            await self.services.bus.emit(subtask_event(run_id, task_of.get(run_id), sub, previous))
        for run_id, task_id, _ in run_ids:
            counts["runs"] += 1
            await self.services.bus.emit(
                EventInput(
                    run_id=run_id,
                    task_id=task_id,
                    type="run.interrupted",
                    status=RunStatus.INTERRUPTED,
                    data={"reason": reason},
                )
            )
        await self.services.approvals.expire_stale("Bosun restarted before a decision was made")
        return counts

    async def shutdown(self, timeout: float = 10.0) -> None:
        contexts = list(self._contexts.values())
        for ctx in contexts:
            ctx.request_stop("shutdown")
        tasks = [c.task for c in contexts if c.task is not None]
        if tasks:
            _, pending = await asyncio.wait(tasks, timeout=timeout)
            for task in pending:
                task.cancel()
            await asyncio.gather(*pending, return_exceptions=True)

    async def wait_idle(self, timeout: float = 30.0) -> None:
        """Test helper: wait until no runs are active."""
        tasks = [c.task for c in self._contexts.values() if c.task is not None]
        if tasks:
            await asyncio.wait_for(asyncio.gather(*tasks, return_exceptions=True), timeout)

    def context(self, run_id: str) -> RunContext | None:
        return self._contexts.get(run_id)


def _kill_if_claude(pid: int) -> bool:
    """Terminate an orphaned Claude Code process left by a crashed previous instance.

    Only processes Bosun started are touched: the pid must still be a
    `claude` process whose environment carries Bosun's client tag, so a
    recycled pid never takes down an unrelated program or the user's own session.
    """
    try:
        cmdline = (
            Path(f"/proc/{pid}/cmdline").read_bytes().replace(b"\0", b" ").decode(errors="replace")
        )
        environ = Path(f"/proc/{pid}/environ").read_bytes().split(b"\0")
    except OSError:
        return False
    if "claude" not in cmdline:
        return False
    if not any(item.startswith(CLIENT_APP_ENV.encode() + b"=bosun") for item in environ):
        return False
    try:
        os.kill(pid, signal.SIGTERM)
    except OSError:
        return False
    return True
