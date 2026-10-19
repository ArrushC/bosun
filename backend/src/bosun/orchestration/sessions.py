"""Run one agent session: persist it, execute it through an adapter, record the outcome."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from bosun.agents.contracts import (
    AgentAdapter,
    ErrorKind,
    ProviderRuntime,
    SessionOutcome,
    SessionSpec,
    SubagentSpec,
)
from bosun.agents.permissions import READ_TOOLS, tool_policy
from bosun.agents.prompts import system_rules
from bosun.db.models import AgentSession
from bosun.domain.enums import SessionKind, SessionStatus
from bosun.events.bus import EventInput
from bosun.ids import new_id, utcnow
from bosun.orchestration.config import RoleConfig, RunConfig
from bosun.orchestration.context import project_notes
from bosun.orchestration.runtime import LiveSession, RunContext

log = logging.getLogger(__name__)


@dataclass
class SessionRequest:
    ctx: RunContext
    config: RunConfig
    role: RoleConfig
    prompt: str
    cwd: Path
    worktree_root: Path
    branch: str | None
    isolated: bool
    kind: SessionKind = SessionKind.AGENT
    name: str | None = None
    subtask_id: str | None = None
    parent_session_id: str | None = None
    output_schema: dict[str, Any] | None = None
    resume: AgentSession | None = None
    attempt: int = 1
    allow_subagents: bool = False
    scenario: dict[str, Any] = field(default_factory=dict)
    # Called once the session row exists (used to emit routed messages to it).
    on_created: Callable[[str], Awaitable[None]] | None = None


class SessionRunner:
    def __init__(self, services: Any) -> None:
        self.services = services

    def _adapter(self, config: RunConfig) -> AgentAdapter:
        return self.services.adapters[config.backend]

    def _subagents(self, config: RunConfig) -> dict[str, SubagentSpec]:
        execution = config.execution_settings()
        result: dict[str, SubagentSpec] = {}
        for role in config.roles.values():
            if role.is_coordinator:
                continue
            policy = tool_policy(
                role.permission_profile, execution, extra_allowed=role.extra_allowed_tools
            )
            result[role.key] = SubagentSpec(
                description=role.description,
                prompt=role.system_prompt,
                tools=[t for t in policy.tools if t not in ("Agent", "Task")] or list(READ_TOOLS),
                model=role.model,
            )
        return result

    def build_spec(
        self, req: SessionRequest, session_id: str, provider: ProviderRuntime
    ) -> SessionSpec:
        config, role = req.config, req.role
        allow_subagents = req.allow_subagents and not role.is_coordinator
        policy = tool_policy(
            role.permission_profile,
            config.execution_settings(),
            extra_allowed=role.extra_allowed_tools,
            allow_subagents=allow_subagents,
        )
        system_append = system_rules(
            role_name=role.name,
            role_prompt=role.system_prompt,
            task_key=req.ctx.task_key,
            cwd=str(req.cwd),
            branch=req.branch,
            isolated=req.isolated,
        ) + project_notes(req.worktree_root, config.trusted_workspace)
        return SessionSpec(
            session_id=session_id,
            run_id=req.ctx.run_id,
            task_id=req.ctx.task_id,
            role=role.key,
            prompt=req.prompt,
            system_prompt_append=system_append,
            cwd=req.cwd,
            allowed_roots=[req.worktree_root],
            protected_roots=[
                self.services.cfg.data_dir,
                *([req.ctx.source_root] if req.isolated and req.ctx.source_root else []),
            ],
            tools=policy.tools,
            auto_allow=policy.auto_allow,
            disallowed_tools=policy.disallowed,
            provider=provider,
            model=role.model or config.model,
            effort=role.effort or config.effort,
            max_turns=role.max_turns or config.max_turns,
            timeout_s=config.timeout_minutes * 60.0,
            resume_sdk_session_id=req.resume.sdk_session_id if req.resume else None,
            output_schema=req.output_schema,
            subagents=self._subagents(config) if allow_subagents else None,
            setting_sources=["project", "local"] if config.trusted_workspace else [],
            sandbox_bash=config.sandbox_bash,
            scenario={**config.demo_scenario, **req.scenario},
        )

    async def run(
        self, req: SessionRequest, provider: ProviderRuntime
    ) -> tuple[str, SessionOutcome]:
        ctx = req.ctx
        db, bus = self.services.db, self.services.bus

        if req.resume is not None:
            session_id = req.resume.id
            async with db.session() as session:
                row = await session.get(AgentSession, session_id)
                assert row is not None
                row.status = SessionStatus.PENDING
                row.turn_count += 1
                row.error = None
                row.error_kind = None
                await session.commit()
        else:
            session_id = new_id("ses")
            async with db.session() as session:
                session.add(
                    AgentSession(
                        id=session_id,
                        run_id=ctx.run_id,
                        subtask_id=req.subtask_id,
                        parent_session_id=req.parent_session_id,
                        kind=req.kind,
                        role=req.role.key,
                        name=req.name or req.role.name,
                        status=SessionStatus.PENDING,
                        cwd=str(req.cwd),
                        branch=req.branch,
                        prompt=req.prompt[:20_000],
                        attempt=req.attempt,
                        turn_count=1,
                    )
                )
                await session.commit()

        common = {
            "run_id": ctx.run_id,
            "task_id": ctx.task_id,
            "session_id": session_id,
            "agent_id": session_id,
            "parent_agent_id": req.parent_session_id,
        }
        await bus.emit(
            EventInput(
                **common,
                type="session.created" if req.resume is None else "session.resumed",
                status="pending",
                data={
                    "role": req.role.key,
                    "role_name": req.role.name,
                    "color": req.role.color,
                    "name": req.name or req.role.name,
                    "kind": req.kind,
                    "subtask_id": req.subtask_id,
                    "cwd": str(req.cwd),
                    "branch": req.branch,
                    "attempt": req.attempt,
                    "prompt": req.prompt[:4000],
                    "permission_profile": req.role.permission_profile,
                },
            )
        )

        if req.on_created is not None:
            await req.on_created(session_id)

        if ctx.stop_event.is_set():
            outcome = SessionOutcome(
                status=SessionStatus.CANCELLED, error="Run stopped before the session started"
            )
            await self._finish(session_id, req, outcome)
            return session_id, outcome

        live = LiveSession(
            services=self.services,
            ctx=ctx,
            session_id=session_id,
            parent_session_id=req.parent_session_id,
            subtask_id=req.subtask_id,
            cwd=req.cwd,
            role=req.role.key,
        )
        # Registered before waiting for a slot so a pause or cancel reaches it.
        ctx.sessions[session_id] = live
        try:
            async with self.services.limiter.slot():
                if live.stop_event.is_set():
                    outcome = SessionOutcome(
                        status=SessionStatus.CANCELLED
                        if ctx.stop_reason == "cancel"
                        else SessionStatus.INTERRUPTED,
                        error=None
                        if ctx.stop_reason != "cancel"
                        else "Run stopped before the session started",
                    )
                else:
                    await self.services.store.set_session_status(
                        session_id, SessionStatus.RUNNING, started_at=utcnow()
                    )
                    spec = self.build_spec(req, session_id, provider)
                    try:
                        outcome = await self._adapter(req.config).run(spec, live)
                    except Exception as exc:  # noqa: BLE001 - adapters must never take down the run loop
                        log.exception("Agent adapter crashed for session %s", session_id)
                        outcome = SessionOutcome(
                            status=SessionStatus.FAILED,
                            error=f"Agent backend crashed: {exc}",
                            error_kind=ErrorKind.PROCESS_CRASH,
                        )
        finally:
            ctx.sessions.pop(session_id, None)
            while not live.inbox.empty():
                ctx.undelivered.append((session_id, live.inbox.get_nowait()))
        await self._finish(session_id, req, outcome)
        return session_id, outcome

    async def _finish(self, session_id: str, req: SessionRequest, outcome: SessionOutcome) -> None:
        db, bus = self.services.db, self.services.bus
        async with db.session() as session:
            row = await session.get(AgentSession, session_id)
            assert row is not None
            row.status = outcome.status
            row.sdk_session_id = outcome.sdk_session_id or row.sdk_session_id
            row.model = outcome.model or row.model
            row.result = outcome.result
            if outcome.structured_output is not None:
                row.structured_output = outcome.structured_output
            row.error = outcome.error
            row.error_kind = outcome.error_kind
            row.num_turns = (row.num_turns or 0) + outcome.num_turns
            row.duration_ms = (row.duration_ms or 0) + (outcome.duration_ms or 0)
            usage = dict(row.usage or {})
            for key, value in outcome.usage.items():
                if isinstance(value, int):
                    usage[key] = usage.get(key, 0) + value
            row.usage = usage
            if outcome.cost_usd is not None:
                row.cost_usd = (row.cost_usd or 0.0) + outcome.cost_usd
            row.api_key_source = outcome.api_key_source or row.api_key_source
            row.ended_at = utcnow()
            row.pid = None
            await session.commit()
            parent = row.parent_session_id
        event_type = {
            SessionStatus.COMPLETED: "session.completed",
            SessionStatus.FAILED: "session.failed",
            SessionStatus.INTERRUPTED: "session.interrupted",
            SessionStatus.CANCELLED: "session.cancelled",
        }.get(outcome.status, "session.status")
        await bus.emit(
            EventInput(
                run_id=req.ctx.run_id,
                task_id=req.ctx.task_id,
                session_id=session_id,
                agent_id=session_id,
                parent_agent_id=parent,
                type=event_type,
                status=outcome.status,
                data={
                    "result": (outcome.result or "")[:8000],
                    "error": outcome.error,
                    "error_kind": outcome.error_kind,
                    "num_turns": outcome.num_turns,
                    "duration_ms": outcome.duration_ms,
                    "usage": outcome.usage,
                    "model": outcome.model,
                },
            )
        )
        bus.notify("run", req.ctx.run_id)
