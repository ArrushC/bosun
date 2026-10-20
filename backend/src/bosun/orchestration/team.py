"""Application-managed multi-agent coordination.

1. A **coordinator** session (read-only) studies the task and returns a plan as
   structured output: subtasks with roles, dependencies and whether they modify code.
2. The plan is validated (roles, keys, dependency cycles, team size) and, when
   configured, held at a **human approval checkpoint**.
3. A **scheduler** runs subtasks as separate Claude sessions, in parallel where
   dependencies allow. Write-capable subtasks get their own git worktree and are
   merged back into the run branch when they finish; conflicts stop for a human
   decision instead of being resolved silently.
4. Reports are **routed explicitly** by Bosun: dependency reports are
   passed into dependants' prompts and all reports go back to the coordinator,
   which writes the final summary. Every routed report is an `agent.message` event —
   agents never communicate except through these observable messages.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sqlalchemy import select

from bosun.agents.contracts import ErrorKind, ProviderRuntime, SessionOutcome
from bosun.agents.prompts import (
    PLAN_SCHEMA,
    TaskContext,
    aggregation_prompt,
    coordinator_prompt,
    plan_from_text,
    subtask_prompt,
)
from bosun.db.models import AgentRun, AgentSession, Subtask
from bosun.domain.enums import (
    TERMINAL_SUBTASK_STATUSES,
    ApprovalKind,
    ApprovalStatus,
    ExecutionMode,
    RunStatus,
    SessionKind,
    SessionStatus,
    SubtaskStatus,
)
from bosun.events.bus import EventInput
from bosun.orchestration.config import RoleConfig, RunConfig
from bosun.orchestration.runtime import RunContext
from bosun.orchestration.sessions import SessionRequest

log = logging.getLogger(__name__)

PLAN_APPROVAL_TIMEOUT_S = 24 * 3600.0
CONTINUE_PROMPT = (
    "Your previous session was interrupted before you finished. Continue the assignment from where you "
    "left off, then finish with the report format requested earlier."
)


class PlanError(ValueError):
    pass


@dataclass
class RunInfo:
    run_id: str
    task_id: str
    task_key: str
    task_title: str
    cwd: Path
    worktree_root: Path
    branch: str | None
    isolation: str


@dataclass
class TeamResult:
    status: str  # completed | failed | stopped | rejected
    summary: str | None = None
    error: str | None = None
    error_kind: str | None = None


def normalise_plan(
    plan: dict[str, Any], *, roles: dict[str, RoleConfig], allowed_roles: list[str], max_size: int
) -> tuple[dict[str, Any], list[str]]:
    """Validate a coordinator plan. Returns (normalised plan, warnings)."""
    warnings: list[str] = []
    raw = plan.get("subtasks")
    if not isinstance(raw, list) or not raw:
        raise PlanError("The coordinator's plan contained no subtasks")
    allowed = [r for r in allowed_roles if r in roles and not roles[r].is_coordinator]
    if not allowed:
        raise PlanError("No agent roles are enabled for team execution")

    def pick_role(name: str, modifies: bool) -> str:
        if name in allowed:
            return name
        lowered = name.lower()
        for key in allowed:
            if key in lowered or lowered in key:
                return key
        fallback = (
            next((k for k in allowed if roles[k].permission_profile != "read_only"), allowed[0])
            if modifies
            else next(
                (k for k in allowed if roles[k].permission_profile == "read_only"), allowed[0]
            )
        )
        warnings.append(f"Unknown role '{name}' mapped to '{fallback}'")
        return fallback

    subtasks: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, item in enumerate(raw):
        if not isinstance(item, dict):
            continue
        key = str(item.get("key") or f"s{index + 1}").strip() or f"s{index + 1}"
        while key in seen:
            key = f"{key}_{index + 1}"
        seen.add(key)
        modifies = bool(item.get("modifies_code"))
        role = pick_role(str(item.get("role") or ""), modifies)
        if modifies and roles[role].permission_profile == "read_only":
            warnings.append(
                f"Subtask {key} modifies code but role '{role}' is read-only; marked read-only"
            )
            modifies = False
        subtasks.append(
            {
                "key": key,
                "title": str(item.get("title") or f"Subtask {key}")[:200],
                "role": role,
                "instructions": str(item.get("instructions") or ""),
                "depends_on": [str(d) for d in item.get("depends_on") or []],
                "modifies_code": modifies,
            }
        )
    if len(subtasks) > max_size:
        warnings.append(f"Plan had {len(subtasks)} subtasks; kept the first {max_size}")
        subtasks = subtasks[:max_size]
    keys = {s["key"] for s in subtasks}
    for s in subtasks:
        dropped = [d for d in s["depends_on"] if d not in keys or d == s["key"]]
        if dropped:
            warnings.append(f"Removed unknown dependencies {dropped} from {s['key']}")
        s["depends_on"] = [d for d in dict.fromkeys(s["depends_on"]) if d in keys and d != s["key"]]

    # Kahn's algorithm: reject cycles.
    indegree = {s["key"]: len(s["depends_on"]) for s in subtasks}
    dependants: dict[str, list[str]] = {k: [] for k in keys}
    for s in subtasks:
        for d in s["depends_on"]:
            dependants[d].append(s["key"])
    ready = [k for k, n in indegree.items() if n == 0]
    visited = 0
    while ready:
        node = ready.pop()
        visited += 1
        for nxt in dependants[node]:
            indegree[nxt] -= 1
            if indegree[nxt] == 0:
                ready.append(nxt)
    if visited != len(subtasks):
        raise PlanError("The coordinator's plan has circular dependencies")

    strategy = (
        plan.get("strategy")
        if plan.get("strategy") in ("single", "team")
        else ("single" if len(subtasks) == 1 else "team")
    )
    if len(subtasks) == 1:
        strategy = "single"
    return {
        "summary": str(plan.get("summary") or ""),
        "rationale": str(plan.get("rationale") or ""),
        "strategy": strategy,
        "subtasks": subtasks,
    }, warnings


class TeamExecutor:
    def __init__(
        self,
        manager: Any,
        ctx: RunContext,
        info: RunInfo,
        config: RunConfig,
        provider: ProviderRuntime,
        task_ctx: TaskContext,
    ) -> None:
        self.manager = manager
        self.services = manager.services
        self.ctx = ctx
        self.info = info
        self.config = config
        self.provider = provider
        self.task_ctx = task_ctx
        self.in_place_write_lock = asyncio.Lock()

    # -- helpers -------------------------------------------------------------------
    async def _emit(
        self,
        type_: str,
        data: dict[str, Any],
        *,
        status: str | None = None,
        session_id: str | None = None,
        agent_id: str | None = None,
        parent_agent_id: str | None = None,
    ) -> None:
        await self.services.bus.emit(
            EventInput(
                run_id=self.ctx.run_id,
                task_id=self.ctx.task_id,
                session_id=session_id,
                agent_id=agent_id,
                parent_agent_id=parent_agent_id,
                type=type_,
                status=status,
                data=data,
            )
        )

    async def _subtasks(self) -> list[Subtask]:
        async with self.services.db.session() as session:
            return list(
                (
                    await session.scalars(
                        select(Subtask)
                        .where(Subtask.run_id == self.ctx.run_id)
                        .order_by(Subtask.position)
                    )
                ).all()
            )

    async def _set_subtask(self, subtask_id: str, status: SubtaskStatus, **fields: Any) -> Subtask:
        async with self.services.db.session() as session:
            row = await session.get(Subtask, subtask_id)
            assert row is not None
            previous = row.status
            row.status = status
            for key, value in fields.items():
                setattr(row, key, value)
            await session.commit()
        if previous != status or "report" in fields:
            await self._emit(
                "subtask.status",
                {
                    "subtask_id": row.id,
                    "key": row.key,
                    "title": row.title,
                    "role": row.role,
                    "from": previous,
                    "to": status,
                    "attempt": row.attempts,
                    "session_id": row.session_id,
                    "error": row.error,
                    "depends_on": row.depends_on,
                },
                status=status,
                agent_id=f"subtask:{row.id}",
            )
        self.services.bus.notify("run", self.ctx.run_id)
        return row

    async def _coordinator_session(self) -> AgentSession | None:
        async with self.services.db.session() as session:
            return await session.scalar(
                select(AgentSession)
                .where(
                    AgentSession.run_id == self.ctx.run_id,
                    AgentSession.kind == SessionKind.COORDINATOR,
                )
                .order_by(AgentSession.created_at.desc())
            )

    # -- phase 1: plan -----------------------------------------------------------------
    async def plan(self) -> tuple[dict[str, Any] | None, TeamResult | None]:
        existing = await self._subtasks()
        if existing:
            async with self.services.db.session() as session:
                run = await session.get(AgentRun, self.ctx.run_id)
                plan = (run.config or {}).get("plan") if run else None
            return plan or {"strategy": "team", "subtasks": []}, None

        await self.services.store.set_run_status(self.ctx.run_id, RunStatus.PLANNING)
        self.ctx.active_status = RunStatus.PLANNING
        coordinator = self.config.coordinator()
        allow_single = self.config.mode == ExecutionMode.AUTO
        team_roles = [
            {
                "key": k,
                "name": self.config.roles[k].name,
                "description": self.config.roles[k].description,
            }
            for k in self.config.team_roles
            if k in self.config.roles
        ]
        session_id, outcome = await self.manager.sessions.run(
            SessionRequest(
                ctx=self.ctx,
                config=self.config,
                role=coordinator,
                kind=SessionKind.COORDINATOR,
                name="Coordinator",
                prompt=coordinator_prompt(
                    self.task_ctx, team_roles, self.config.max_team_size, allow_single
                ),
                cwd=self.info.cwd,
                worktree_root=self.info.worktree_root,
                branch=self.info.branch,
                isolated=self.info.isolation == "worktree",
                output_schema=PLAN_SCHEMA,
                scenario={"roles": self.config.team_roles, "task_title": self.info.task_title},
            ),
            self.provider,
        )
        if outcome.status == SessionStatus.INTERRUPTED or self.ctx.stop_event.is_set():
            return None, TeamResult("stopped")
        if outcome.status != SessionStatus.COMPLETED:
            return None, TeamResult(
                "failed",
                error=f"Coordinator failed: {outcome.error}",
                error_kind=outcome.error_kind,
            )

        raw = (
            outcome.structured_output
            if isinstance(outcome.structured_output, dict)
            else plan_from_text(outcome.result or "")
        )
        if not raw:
            return None, TeamResult(
                "failed",
                error="The coordinator did not return a usable plan",
                error_kind=ErrorKind.INVALID_REQUEST,
            )
        try:
            plan, warnings = normalise_plan(
                raw,
                roles=self.config.roles,
                allowed_roles=self.config.team_roles,
                max_size=self.config.max_team_size,
            )
        except PlanError as exc:
            return None, TeamResult("failed", error=str(exc), error_kind=ErrorKind.INVALID_REQUEST)

        async with self.services.db.session() as session:
            run = await session.get(AgentRun, self.ctx.run_id)
            assert run is not None
            for position, item in enumerate(plan["subtasks"]):
                session.add(
                    Subtask(
                        run_id=self.ctx.run_id,
                        key=item["key"],
                        position=position,
                        title=item["title"],
                        role=item["role"],
                        instructions=item["instructions"],
                        depends_on=item["depends_on"],
                        modifies_code=item["modifies_code"],
                        max_attempts=self.config.subtask_max_attempts,
                    )
                )
            run.strategy = plan["strategy"]
            run.config = {**run.config, "plan": plan, "plan_warnings": warnings}
            await session.commit()
        subtasks = await self._subtasks()
        await self._emit(
            "plan.created",
            {
                "summary": plan["summary"],
                "rationale": plan["rationale"],
                "strategy": plan["strategy"],
                "warnings": warnings,
                "subtasks": [
                    {
                        "subtask_id": s.id,
                        "key": s.key,
                        "title": s.title,
                        "role": s.role,
                        "depends_on": s.depends_on,
                        "modifies_code": s.modifies_code,
                        "instructions": s.instructions[:2000],
                    }
                    for s in subtasks
                ],
            },
            session_id=session_id,
            agent_id=session_id,
        )
        return plan, None

    async def approve_plan(self, plan: dict[str, Any]) -> bool:
        approval = await self.services.approvals.create(
            self.ctx,
            kind=ApprovalKind.PLAN,
            title=f"Approve the team plan for {self.info.task_key}",
            description=plan.get("summary"),
            data={"plan": plan},
            timeout_s=PLAN_APPROVAL_TIMEOUT_S,
        )
        await self.services.store.set_run_status(
            self.ctx.run_id, RunStatus.WAITING_APPROVAL, reason="Waiting for plan approval"
        )
        resolution = await self.services.approvals.wait(self.ctx, approval, PLAN_APPROVAL_TIMEOUT_S)
        return resolution.get("decision") == "approve"

    # -- phase 2: schedule -----------------------------------------------------------------
    async def schedule(self) -> None:
        self.ctx.active_status = RunStatus.RUNNING
        await self.services.store.set_run_status(self.ctx.run_id, RunStatus.RUNNING, reason=None)
        running: dict[str, asyncio.Task[None]] = {}
        try:
            while True:
                subtasks = await self._subtasks()
                by_key = {s.key: s for s in subtasks}
                if self.ctx.stop_event.is_set():
                    break
                started = False
                for s in subtasks:
                    if s.id in running or s.status not in (
                        SubtaskStatus.PENDING,
                        SubtaskStatus.WAITING_DEPENDENCY,
                    ):
                        continue
                    deps = [by_key[k] for k in s.depends_on if k in by_key]
                    if all(
                        d.status in (SubtaskStatus.COMPLETED, SubtaskStatus.SKIPPED) for d in deps
                    ):
                        if len(running) < self.config.max_parallel:
                            running[s.id] = asyncio.create_task(
                                self._run_subtask(s.id), name=f"subtask:{s.key}"
                            )
                            started = True
                    elif s.status == SubtaskStatus.PENDING:
                        await self._set_subtask(s.id, SubtaskStatus.WAITING_DEPENDENCY)
                if not running:
                    break
                if started:
                    await asyncio.sleep(0)
                done, _ = await asyncio.wait(running.values(), return_when=asyncio.FIRST_COMPLETED)
                for task in done:
                    crashed = [k for k, v in running.items() if v is task]
                    running = {k: v for k, v in running.items() if v is not task}
                    if (exc := task.exception()) is not None:
                        log.error("Subtask runner crashed", exc_info=exc)
                        # Mark it failed so the scheduler does not start it again in a loop.
                        for subtask_id in crashed:
                            await self._fail_crashed(subtask_id, exc)
        finally:
            if running:
                await asyncio.gather(*running.values(), return_exceptions=True)

    async def _fail_crashed(self, subtask_id: str, exc: BaseException) -> None:
        try:
            current = next((s for s in await self._subtasks() if s.id == subtask_id), None)
            if current is None or current.status in TERMINAL_SUBTASK_STATUSES:
                return
            if current.status not in (SubtaskStatus.RUNNING, SubtaskStatus.WAITING_APPROVAL):
                await self._set_subtask(subtask_id, SubtaskStatus.RUNNING)
            await self._set_subtask(
                subtask_id, SubtaskStatus.FAILED, error=f"Internal error: {exc}"
            )
        except Exception:  # noqa: BLE001
            log.exception("Could not mark crashed subtask %s as failed", subtask_id)

    async def _routed(
        self, sender: str | None, recipient: str, kind: str, text: str, subtask_title: str
    ) -> None:
        await self._emit(
            "agent.message",
            {
                "from_session_id": sender,
                "to_session_id": recipient,
                "kind": kind,
                "subtask": subtask_title,
                "text": text[:6000],
                "routed_by": "bosun",
            },
            session_id=recipient,
            agent_id=sender,
        )

    async def _run_subtask(self, subtask_id: str) -> None:
        async with self.services.db.session() as session:
            s = await session.get(Subtask, subtask_id)
            assert s is not None
            prior = await session.get(AgentSession, s.session_id) if s.session_id else None
        if _finished_but_unrecorded(s, prior):
            # The agent finished, but Bosun stopped before the subtask was recorded as
            # done (possibly mid-merge). Record it from the session instead of running it
            # again on top of its own work.
            assert prior is not None
            await self._emit(
                "subtask.recovered",
                {"subtask_id": s.id, "key": s.key, "session_id": prior.id},
                session_id=prior.id,
                agent_id=f"subtask:{s.id}",
            )
            outcome = SessionOutcome(
                status=SessionStatus.COMPLETED,
                sdk_session_id=prior.sdk_session_id,
                result=prior.result,
            )
            await self._after_subtask(s.id, prior.id, outcome, s.attempts, recovered=True)
            return
        attempt = s.attempts + 1
        role = self.config.roles.get(s.role) or next(iter(self.config.roles.values()))
        coordinator = await self._coordinator_session()

        resume = (
            prior
            if prior is not None
            and prior.status == SessionStatus.INTERRUPTED
            and prior.sdk_session_id
            and prior.cwd
            and Path(prior.cwd).exists()
            else None
        )
        cwd, root, branch = self.info.cwd, self.info.worktree_root, self.info.branch
        if resume is not None:
            cwd = Path(resume.cwd or self.info.cwd)
            root = Path(s.worktree_path) if s.worktree_path else self.info.worktree_root
            branch = s.branch or self.info.branch
        elif s.modifies_code and self.info.isolation == "worktree" and self.info.branch:
            async with self.ctx.merge_lock:
                child_cwd, child_branch = await self.services.worktrees.create_child(
                    self.info.cwd, self.info.branch, self.ctx.run_id, s.key
                )
            child_root = await self.services.worktrees.repo_root(child_cwd)
            cwd, root, branch = child_cwd, child_root, child_branch
            s = await self._set_subtask(
                s.id,
                SubtaskStatus.RUNNING,
                attempts=attempt,
                worktree_path=str(child_root),
                branch=child_branch,
            )
        if s.status != SubtaskStatus.RUNNING:
            s = await self._set_subtask(s.id, SubtaskStatus.RUNNING, attempts=attempt, error=None)

        deps_reports: list[tuple[str, str, str]] = []
        dep_sessions: list[tuple[str | None, str, str]] = []
        for dep in await self._subtasks():
            if dep.key in s.depends_on and dep.status == SubtaskStatus.COMPLETED and dep.report:
                who = (
                    self.config.roles[dep.role].name if dep.role in self.config.roles else dep.role
                )
                deps_reports.append((who, dep.title, dep.report))
                dep_sessions.append((dep.session_id, dep.title, dep.report))

        prompt = (
            CONTINUE_PROMPT
            if resume
            else subtask_prompt(
                self.task_ctx,
                title=s.title,
                instructions=s.instructions,
                dependency_reports=deps_reports,
                cwd=str(cwd),
            )
        )
        request = SessionRequest(
            ctx=self.ctx,
            config=self.config,
            role=role,
            prompt=prompt,
            cwd=cwd,
            worktree_root=root,
            branch=branch,
            isolated=self.info.isolation == "worktree",
            name=f"{role.name}: {s.title}",
            subtask_id=s.id,
            parent_session_id=coordinator.id if coordinator else None,
            resume=resume,
            attempt=attempt,
            allow_subagents=self.config.allow_subagents,
            scenario={"task_title": self.info.task_title},
        )

        async def run_session() -> tuple[str, SessionOutcome]:
            if s.modifies_code and self.info.isolation != "worktree":
                async with self.in_place_write_lock:
                    return await self._start_with_messages(request, coordinator, s, dep_sessions)
            return await self._start_with_messages(request, coordinator, s, dep_sessions)

        session_id, outcome = await run_session()
        await self._after_subtask(s.id, session_id, outcome, attempt)

    async def _start_with_messages(
        self,
        request: SessionRequest,
        coordinator: AgentSession | None,
        s: Subtask,
        dep_sessions: list[tuple[str | None, str, str]],
    ) -> tuple[str, SessionOutcome]:
        manager = self.manager

        async def on_created(session_id: str) -> None:
            await self._set_subtask(s.id, SubtaskStatus.RUNNING, session_id=session_id)
            if request.resume is None:
                await self._routed(
                    coordinator.id if coordinator else None,
                    session_id,
                    "assignment",
                    s.instructions,
                    s.title,
                )
                for sender, title, report in dep_sessions:
                    await self._routed(sender, session_id, "report", report, title)

        request.on_created = on_created
        return await manager.sessions.run(request, self.provider)

    async def _after_subtask(
        self,
        subtask_id: str,
        session_id: str,
        outcome: SessionOutcome,
        attempt: int,
        *,
        recovered: bool = False,
    ) -> None:
        async with self.services.db.session() as session:
            s = await session.get(Subtask, subtask_id)
            assert s is not None
        if outcome.status == SessionStatus.COMPLETED:
            if s.modifies_code and s.worktree_path and s.branch and self.info.branch:
                merged = await self._merge_child(s, outcome, recovered=recovered)
                if not merged:
                    return
            await self._set_subtask(
                s.id, SubtaskStatus.COMPLETED, report=outcome.result or "(no report)", error=None
            )
            return
        if (
            outcome.status in (SessionStatus.INTERRUPTED, SessionStatus.CANCELLED)
            or self.ctx.stop_event.is_set()
        ):
            await self._set_subtask(s.id, SubtaskStatus.PENDING, error="Interrupted")
            return
        retryable = outcome.error_kind not in ErrorKind.NON_RETRYABLE
        if retryable and attempt < s.max_attempts:
            await self._emit(
                "subtask.retry",
                {"subtask_id": s.id, "key": s.key, "attempt": attempt, "error": outcome.error},
                status="retrying",
                agent_id=f"subtask:{s.id}",
            )
            await self._set_subtask(
                s.id, SubtaskStatus.PENDING, error=outcome.error, session_id=None
            )
            return
        await self._set_subtask(s.id, SubtaskStatus.FAILED, error=outcome.error or "Agent failed")

    async def _merge_child(
        self, s: Subtask, outcome: SessionOutcome, *, recovered: bool = False
    ) -> bool:
        worktrees = self.services.worktrees
        child_cwd = Path(s.worktree_path or "")
        message = f"{self.info.task_key} {s.key}: {s.title}"
        async with self.ctx.merge_lock:
            commit = await worktrees.commit_all(child_cwd, message) if child_cwd.is_dir() else None
            # Nothing new to commit and nothing unmerged: the subtask made no changes, or
            # (when recovering) its changes were merged before Bosun stopped.
            if commit is None and await worktrees.contains(self.info.cwd, s.branch or ""):
                if not recovered:
                    await self._emit(
                        "subtask.no_changes",
                        {"subtask_id": s.id, "key": s.key},
                        agent_id=f"subtask:{s.id}",
                    )
                return True
            ok, conflicts, merge_commit = await worktrees.merge_into(
                self.info.cwd, s.branch or "", f"Merge {message}"
            )
        if ok:
            await self._set_subtask(s.id, SubtaskStatus.RUNNING, merge_commit=merge_commit)
            await self._emit(
                "artifact.commit",
                {
                    "subtask_id": s.id,
                    "key": s.key,
                    "commit": commit,
                    "merge_commit": merge_commit,
                    "branch": s.branch,
                },
                status="completed",
                session_id=s.session_id,
                agent_id=s.session_id,
            )
            return True

        await self._set_subtask(
            s.id,
            SubtaskStatus.CONFLICT,
            report=outcome.result,
            error=f"Merge conflict in {', '.join(conflicts) or 'files'}",
        )
        approval = await self.services.approvals.create(
            self.ctx,
            kind=ApprovalKind.INTEGRATION,
            title=f"Merge conflict: {s.title}",
            description=(
                f"{s.key} changed files that conflict with work already merged: {', '.join(conflicts[:8])}. "
                "Approve to re-run this subtask on top of the latest run branch; deny to skip it."
            ),
            session_id=s.session_id,
            subtask_id=s.id,
            data={"conflicts": conflicts, "branch": s.branch},
            timeout_s=PLAN_APPROVAL_TIMEOUT_S,
        )
        resolution = await self.services.approvals.wait(self.ctx, approval, PLAN_APPROVAL_TIMEOUT_S)
        status = resolution.get("status")
        if resolution.get("decision") == "approve":
            await self._set_subtask(
                s.id, SubtaskStatus.PENDING, error=None, session_id=None, worktree_path=None
            )
        elif status == ApprovalStatus.CANCELLED:
            # The run stopped before anyone decided: re-run on the latest branch on resume.
            await self._set_subtask(
                s.id,
                SubtaskStatus.PENDING,
                error="Merge conflict; re-runs on the latest run branch when resumed",
                session_id=None,
                worktree_path=None,
            )
        elif status == ApprovalStatus.EXPIRED:
            # Nobody decided in time; leave it in conflict for the operator to retry or skip.
            await self._set_subtask(
                s.id,
                SubtaskStatus.CONFLICT,
                error=f"Merge conflict in {', '.join(conflicts) or 'files'}; no decision before the timeout",
            )
        else:
            await self._set_subtask(
                s.id, SubtaskStatus.SKIPPED, error="Skipped after merge conflict"
            )
        return False

    # -- phase 3: aggregate -----------------------------------------------------------------
    async def aggregate(self, strategy: str) -> TeamResult:
        subtasks = await self._subtasks()
        failed = [
            s
            for s in subtasks
            if s.status in (SubtaskStatus.FAILED, SubtaskStatus.CONFLICT, SubtaskStatus.CANCELLED)
        ]
        unfinished = [s for s in subtasks if s.status not in TERMINAL_SUBTASK_STATUSES]
        if self.ctx.stop_event.is_set():
            return TeamResult("stopped")
        if failed or unfinished:
            names = ", ".join(f"{s.key} ({s.status})" for s in failed + unfinished)
            return TeamResult(
                "failed", error=f"Subtasks did not complete: {names}", error_kind=ErrorKind.UNKNOWN
            )

        results = [
            {
                "title": s.title,
                "role": self.config.roles[s.role].name if s.role in self.config.roles else s.role,
                "status": s.status,
                "report": s.report or "",
                "session_id": s.session_id or "",
            }
            for s in subtasks
        ]
        if strategy == "single" or len(subtasks) == 1:
            return TeamResult("completed", summary=results[0]["report"] if results else None)

        coordinator = await self._coordinator_session()
        if coordinator is None or not coordinator.sdk_session_id:
            return TeamResult("completed", summary=_fallback_summary(results))
        for r in results:
            await self._routed(
                r["session_id"] or None, coordinator.id, "report", r["report"], r["title"]
            )
        _, outcome = await self.manager.sessions.run(
            SessionRequest(
                ctx=self.ctx,
                config=self.config,
                role=self.config.coordinator(),
                kind=SessionKind.COORDINATOR,
                prompt=aggregation_prompt(results),
                cwd=self.info.cwd,
                worktree_root=self.info.worktree_root,
                branch=self.info.branch,
                isolated=self.info.isolation == "worktree",
                resume=coordinator,
                scenario={"aggregate": True},
            ),
            self.provider,
        )
        if outcome.status == SessionStatus.COMPLETED and outcome.result:
            return TeamResult("completed", summary=outcome.result)
        if self.ctx.stop_event.is_set():
            return TeamResult("stopped")
        return TeamResult(
            "completed",
            summary=_fallback_summary(results) + "\n\n_(Coordinator summary unavailable.)_",
        )


def _finished_but_unrecorded(s: Subtask, prior: AgentSession | None) -> bool:
    """True when a subtask's session completed but the subtask was never recorded.

    That happens only when Bosun stops between the session ending and the subtask
    being marked done; startup recovery then puts the subtask back to pending with
    its attempt count unchanged. Operator and run retries reset the attempt count
    to 0 and always start a fresh session.
    """
    return (
        prior is not None
        and prior.status == SessionStatus.COMPLETED
        and s.status in (SubtaskStatus.PENDING, SubtaskStatus.WAITING_DEPENDENCY)
        and s.attempts > 0
    )


def _fallback_summary(results: list[dict[str, str]]) -> str:
    return "\n\n".join(f"### {r['title']} — {r['role']}\n{r['report']}" for r in results)
