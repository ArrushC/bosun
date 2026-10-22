"""In-memory runtime state for active runs: sessions, approvals and concurrency."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import shlex
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import select, update

from bosun.agents.contracts import AgentEvent, PermissionDecision, PermissionRequest
from bosun.db.models import AgentRun, AgentSession, ApprovalRequest, Artifact, Subtask
from bosun.db.session import Database
from bosun.domain.enums import (
    ApprovalKind,
    ApprovalStatus,
    RunStatus,
    SessionStatus,
    SubtaskStatus,
)
from bosun.domain.state_machines import InvalidTransition, ensure_run_transition
from bosun.events.bus import EventBus, EventInput
from bosun.ids import utcnow
from bosun.security.redaction import redact

log = logging.getLogger(__name__)

FILE_WRITE_TOOLS = {"Write", "Edit", "MultiEdit", "NotebookEdit"}


class Limiter:
    """Counting limiter whose capacity can change at runtime (Settings → concurrency)."""

    def __init__(self, capacity: int) -> None:
        self.capacity = capacity
        self.in_use = 0
        self._cond = asyncio.Condition()

    @contextlib.asynccontextmanager
    async def slot(self) -> AsyncIterator[None]:
        async with self._cond:
            await self._cond.wait_for(lambda: self.in_use < self.capacity)
            self.in_use += 1
        try:
            yield
        finally:
            async with self._cond:
                self.in_use -= 1
                self._cond.notify_all()

    async def resize(self, capacity: int) -> None:
        async with self._cond:
            self.capacity = max(1, capacity)
            self._cond.notify_all()


@dataclass
class RunContext:
    run_id: str
    task_id: str
    task_key: str
    stop_event: asyncio.Event = field(default_factory=asyncio.Event)
    stop_reason: str | None = None  # "cancel" | "pause"
    sessions: dict[str, LiveSession] = field(default_factory=dict)
    remembered: list[dict[str, str]] = field(default_factory=list)
    pending_approvals: int = 0
    active_status: RunStatus = RunStatus.RUNNING
    merge_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    plan_decision: asyncio.Future[bool] | None = None
    task: asyncio.Task[None] | None = None
    # The user's own checkout; agents in isolated worktrees must not touch it.
    source_root: Path | None = None
    # Operator messages a session never delivered: (session_id, text).
    undelivered: list[tuple[str, str]] = field(default_factory=list)

    def request_stop(self, reason: str) -> None:
        # Cancel and shutdown override an earlier pause; a pause never downgrades a cancel.
        if self.stop_reason is None or reason in ("cancel", "shutdown"):
            self.stop_reason = reason
        self.stop_event.set()
        for live in self.sessions.values():
            live.stop_event.set()


class RunStore:
    """Persistence helpers shared by executors; every change is also an event."""

    def __init__(self, db: Database, bus: EventBus) -> None:
        self.db = db
        self.bus = bus

    async def set_run_status(
        self,
        run_id: str,
        status: RunStatus,
        *,
        reason: str | None = None,
        force: bool = False,
        **fields: Any,
    ) -> AgentRun | None:
        async with self.db.session() as session:
            run = await session.get(AgentRun, run_id)
            if run is None:
                return None
            current = RunStatus(run.status)
            if current != status:
                try:
                    ensure_run_transition(current, status)
                except InvalidTransition:
                    if not force:
                        log.warning(
                            "Ignoring invalid run transition %s -> %s for %s",
                            current,
                            status,
                            run_id,
                        )
                        return run
            run.status = status
            if reason is not None:
                run.status_reason = reason
            for key, value in fields.items():
                setattr(run, key, value)
            if (
                status in (RunStatus.PREPARING, RunStatus.PLANNING, RunStatus.RUNNING)
                and run.started_at is None
            ):
                run.started_at = utcnow()
            if status in (
                RunStatus.COMPLETED,
                RunStatus.FAILED,
                RunStatus.CANCELLED,
                RunStatus.INTERRUPTED,
            ):
                run.ended_at = utcnow()
            elif status == RunStatus.QUEUED:
                run.ended_at = None
            await session.commit()
            task_id = run.task_id
        if current != status:
            await self.bus.emit(
                EventInput(
                    run_id=run_id,
                    task_id=task_id,
                    type="run.status",
                    status=status,
                    data={"from": current, "to": status, "reason": reason},
                )
            )
        self.bus.notify("run", run_id, status=status)
        self.bus.notify("task", task_id)
        return run

    async def update_run(self, run_id: str, **fields: Any) -> None:
        async with self.db.session() as session:
            run = await session.get(AgentRun, run_id)
            if run is None:
                return
            for key, value in fields.items():
                setattr(run, key, value)
            await session.commit()
        self.bus.notify("run", run_id)

    async def set_session_status(
        self, session_id: str, status: SessionStatus, **fields: Any
    ) -> None:
        async with self.db.session() as session:
            row = await session.get(AgentSession, session_id)
            if row is None:
                return
            row.status = status
            for key, value in fields.items():
                setattr(row, key, value)
            await session.commit()
            run_id, task_id, parent = row.run_id, None, row.parent_session_id
        await self.bus.emit(
            EventInput(
                run_id=run_id,
                task_id=task_id,
                session_id=session_id,
                agent_id=session_id,
                parent_agent_id=parent,
                type="session.status",
                status=status,
            )
        )
        self.bus.notify("run", run_id)


class LiveSession:
    """`SessionIO` implementation bound to one persisted `AgentSession`."""

    def __init__(
        self,
        *,
        services: Any,
        ctx: RunContext,
        session_id: str,
        parent_session_id: str | None,
        subtask_id: str | None,
        cwd: Path,
        role: str,
    ) -> None:
        self.services = services
        self.ctx = ctx
        self.session_id = session_id
        self.parent_session_id = parent_session_id
        self.subtask_id = subtask_id
        self.cwd = cwd
        self.role = role
        self.inbox: asyncio.Queue[str] = asyncio.Queue()
        self.stop_event = asyncio.Event()
        if ctx.stop_event.is_set():
            self.stop_event.set()
        self._tool_inputs: dict[str, tuple[str, dict[str, Any]]] = {}
        self._recorded_files: set[str] = set()

    def _agent_ids(self, event: AgentEvent) -> tuple[str, str | None]:
        data = event.data
        if event.type == "subagent.spawned":
            parent_tool = data.get("parent_tool_use_id")
            parent = f"{self.session_id}/{parent_tool}" if parent_tool else self.session_id
            return f"{self.session_id}/{data['tool_use_id']}", parent
        if event.type.startswith("task.") and data.get("tool_use_id"):
            return f"{self.session_id}/{data['tool_use_id']}", self.session_id
        parent_tool = data.get("parent_tool_use_id")
        if parent_tool:
            return f"{self.session_id}/{parent_tool}", self.session_id
        return self.session_id, self.parent_session_id

    async def emit(self, event: AgentEvent) -> None:
        agent_id, parent_agent_id = self._agent_ids(event)
        data = dict(event.data)
        if event.type == "tool.call":
            self._tool_inputs[data["tool_use_id"]] = (data.get("name", ""), data.get("input") or {})
        await self.services.bus.emit(
            EventInput(
                run_id=self.ctx.run_id,
                task_id=self.ctx.task_id,
                session_id=self.session_id,
                agent_id=agent_id,
                parent_agent_id=parent_agent_id,
                type=event.type,
                status=event.status,
                data=data,
            )
        )
        if event.type == "session.init" and data.get("sdk_session_id"):
            async with self.services.db.session() as session:
                row = await session.get(AgentSession, self.session_id)
                if row is not None:
                    row.sdk_session_id = data["sdk_session_id"]
                    row.model = data.get("model") or row.model
                    row.api_key_source = data.get("api_key_source")
                    await session.commit()
        if event.type == "tool.result" and not data.get("is_error"):
            name, tool_input = self._tool_inputs.pop(data.get("tool_use_id", ""), ("", {}))
            if name in FILE_WRITE_TOOLS:
                await self._record_file(name, tool_input, data.get("tool_use_id"), agent_id)

    async def _record_file(
        self, tool: str, tool_input: dict[str, Any], tool_use_id: str | None, agent_id: str
    ) -> None:
        raw = tool_input.get("file_path") or tool_input.get("notebook_path")
        if not isinstance(raw, str):
            return
        path = Path(raw)
        try:
            rel = (
                path.resolve().relative_to(self.cwd.resolve()).as_posix()
                if path.is_absolute()
                else raw
            )
        except ValueError:
            rel = raw
        await self.services.bus.emit(
            EventInput(
                run_id=self.ctx.run_id,
                task_id=self.ctx.task_id,
                session_id=self.session_id,
                agent_id=agent_id,
                parent_agent_id=self.session_id
                if agent_id != self.session_id
                else self.parent_session_id,
                type="artifact.file",
                status="completed",
                data={
                    "path": rel,
                    "tool": tool,
                    "tool_use_id": tool_use_id,
                    "action": "created" if tool == "Write" else "modified",
                },
            )
        )
        if rel in self._recorded_files:
            return
        self._recorded_files.add(rel)
        async with self.services.db.session() as session:
            session.add(
                Artifact(
                    run_id=self.ctx.run_id,
                    session_id=self.session_id,
                    kind="file",
                    title=rel,
                    path=rel,
                    data={"tool": tool, "agent_id": agent_id},
                )
            )
            await session.commit()

    async def request_permission(self, request: PermissionRequest) -> PermissionDecision:
        return await self.services.approvals.request_tool(self, request)

    def set_pid(self, pid: int | None) -> None:
        asyncio.get_running_loop().create_task(self._store_pid(pid))

    async def _store_pid(self, pid: int | None) -> None:
        async with self.services.db.session() as session:
            row = await session.get(AgentSession, self.session_id)
            if row is not None:
                row.pid = pid
                await session.commit()


def _remember_key(tool_name: str, tool_input: dict[str, Any]) -> dict[str, str]:
    if tool_name == "Bash":
        return {"tool": "Bash", "command": str(tool_input.get("command", "")).strip()}
    return {"tool": tool_name}


def _matches(rule: dict[str, str], tool_name: str, tool_input: dict[str, Any]) -> bool:
    return rule == _remember_key(tool_name, tool_input)


def describe_tool_request(tool_name: str, tool_input: dict[str, Any]) -> tuple[str, str | None]:
    if tool_name == "Bash":
        command = str(tool_input.get("command", ""))
        try:
            program = shlex.split(command)[0] if command else ""
        except ValueError:
            program = command.split(" ")[0]
        return f"Run `{program}`" if program else "Run a shell command", tool_input.get(
            "description"
        )
    path = tool_input.get("file_path") or tool_input.get("path") or tool_input.get("url")
    return (f"{tool_name} {path}" if path else f"Use {tool_name}"), None


def subtask_event(
    run_id: str, task_id: str | None, row: Subtask, previous: str | None, **extra: Any
) -> EventInput:
    """The `subtask.status` event for a change to `row` (the run page and graph follow these)."""
    return EventInput(
        run_id=run_id,
        task_id=task_id,
        type="subtask.status",
        status=row.status,
        agent_id=f"subtask:{row.id}",
        data={
            "subtask_id": row.id,
            "key": row.key,
            "kind": row.kind,
            "title": row.title,
            "role": row.role,
            "from": previous,
            "to": row.status,
            "attempt": row.attempts,
            "session_id": row.session_id,
            "error": row.error,
            "depends_on": row.depends_on,
            **extra,
        },
    )


class ApprovalBroker:
    """Human-in-the-loop approvals for tool permissions, plans, integrations and workflow steps.

    Tool, plan and integration requests live as long as the agent or run that asked:
    a waiting coroutine receives the decision, and requests left pending by a restart
    are expired. Workflow approval steps are durable instead. Their request stays
    pending through pauses and restarts, a decision is recorded on the step in the
    same transaction that resolves the request (so it counts once, whether or not the
    run is active), and resuming the run waits on the same request again.
    """

    def __init__(self, services: Any) -> None:
        self.services = services
        self._waiters: dict[str, asyncio.Future[dict[str, Any]]] = {}

    @property
    def db(self) -> Database:
        return self.services.db

    async def _settings_timeout(self) -> float:
        settings = await self.services.load_settings()
        return settings.execution.approval_timeout_minutes * 60.0

    async def _enter_waiting(self, ctx: RunContext, session_id: str | None) -> None:
        ctx.pending_approvals += 1
        if session_id:
            await self.services.store.set_session_status(session_id, SessionStatus.WAITING_APPROVAL)
        if ctx.pending_approvals == 1:
            await self.services.store.set_run_status(ctx.run_id, RunStatus.WAITING_APPROVAL)

    async def _leave_waiting(self, ctx: RunContext, session_id: str | None) -> None:
        ctx.pending_approvals = max(0, ctx.pending_approvals - 1)
        if session_id and not ctx.stop_event.is_set():
            await self.services.store.set_session_status(session_id, SessionStatus.RUNNING)
        if ctx.pending_approvals == 0 and not ctx.stop_event.is_set():
            await self.services.store.set_run_status(ctx.run_id, ctx.active_status)

    async def create(
        self,
        ctx: RunContext,
        *,
        kind: ApprovalKind,
        title: str,
        description: str | None = None,
        session_id: str | None = None,
        subtask_id: str | None = None,
        tool_name: str | None = None,
        tool_use_id: str | None = None,
        tool_input: dict[str, Any] | None = None,
        data: dict[str, Any] | None = None,
        timeout_s: float | None = None,
    ) -> ApprovalRequest:
        async with self.db.session() as session:
            row = ApprovalRequest(
                run_id=ctx.run_id,
                task_id=ctx.task_id,
                session_id=session_id,
                subtask_id=subtask_id,
                kind=kind,
                title=title,
                description=description,
                tool_name=tool_name,
                tool_use_id=tool_use_id,
                tool_input=redact(tool_input, max_string=4000) if tool_input is not None else None,
                data=redact(data or {}, max_string=8000),
                expires_at=utcnow() + timedelta(seconds=timeout_s) if timeout_s else None,
            )
            session.add(row)
            await session.commit()
        self._waiters[row.id] = asyncio.get_running_loop().create_future()
        await self.services.bus.emit(
            EventInput(
                run_id=ctx.run_id,
                task_id=ctx.task_id,
                session_id=session_id,
                agent_id=session_id,
                type="approval.requested",
                status="pending",
                data={
                    "approval_id": row.id,
                    "kind": kind,
                    "title": title,
                    "description": description,
                    "tool_name": tool_name,
                    "tool_use_id": tool_use_id,
                    "tool_input": tool_input,
                    "subtask_id": subtask_id,
                },
            )
        )
        self.services.bus.notify("approval", row.id, action="created")
        return row

    async def wait(
        self, ctx: RunContext, approval: ApprovalRequest, timeout_s: float | None
    ) -> dict[str, Any]:
        """Wait for a human decision, the run being stopped, or the timeout."""
        future = self._waiters[approval.id]
        stop_wait = asyncio.ensure_future(ctx.stop_event.wait())
        try:
            done, _ = await asyncio.wait(
                {future, stop_wait}, timeout=timeout_s, return_when=asyncio.FIRST_COMPLETED
            )
            if future in done:
                return future.result()
            status = ApprovalStatus.CANCELLED if stop_wait in done else ApprovalStatus.EXPIRED
            message = (
                "Run stopped" if stop_wait in done else "No decision before the approval timeout"
            )
            changed = await self._finalise(
                approval.id, status, {"decision": "deny", "message": message}
            )
            if changed is None and future.done() and not future.cancelled():
                return future.result()  # a human decided in the same instant
            return {"decision": "deny", "message": message, "status": status}
        finally:
            stop_wait.cancel()
            self._waiters.pop(approval.id, None)

    async def request_tool(
        self, live: LiveSession, request: PermissionRequest
    ) -> PermissionDecision:
        ctx = live.ctx
        for rule in ctx.remembered:
            if _matches(rule, request.tool_name, request.tool_input):
                await self.services.bus.emit(
                    EventInput(
                        run_id=ctx.run_id,
                        task_id=ctx.task_id,
                        session_id=live.session_id,
                        agent_id=live.session_id,
                        type="approval.auto",
                        status="approved",
                        data={
                            "tool_name": request.tool_name,
                            "tool_use_id": request.tool_use_id,
                            "rule": rule,
                        },
                    )
                )
                return PermissionDecision(allow=True)
        if ctx.stop_event.is_set():
            return PermissionDecision(allow=False, message="Run is stopping", interrupt=True)

        title, subtitle = describe_tool_request(request.tool_name, request.tool_input)
        timeout_s = await self._settings_timeout()
        # Mark the session/run as waiting before the request becomes visible, so
        # observers never see a pending approval on a "running" run.
        await self._enter_waiting(ctx, live.session_id)
        try:
            approval = await self.create(
                ctx,
                kind=ApprovalKind.TOOL_PERMISSION,
                title=request.title or title,
                description=request.description or subtitle or request.decision_reason,
                session_id=live.session_id,
                subtask_id=live.subtask_id,
                tool_name=request.tool_name,
                tool_use_id=request.tool_use_id,
                tool_input=request.tool_input,
                data={
                    "role": live.role,
                    "blocked_path": request.blocked_path,
                    "subagent_id": request.subagent_id,
                },
                timeout_s=timeout_s,
            )
            resolution = await self.wait(ctx, approval, timeout_s)
        finally:
            await self._leave_waiting(ctx, live.session_id)
        if resolution.get("decision") == "approve":
            if resolution.get("remember"):
                ctx.remembered.append(_remember_key(request.tool_name, request.tool_input))
                await self.services.store.update_run(
                    ctx.run_id, config=await self._config_with_remembered(ctx)
                )
            return PermissionDecision(allow=True)
        return PermissionDecision(
            allow=False,
            message=resolution.get("message")
            or "The operator denied this action. Continue without it or explain what you need.",
        )

    async def _config_with_remembered(self, ctx: RunContext) -> dict[str, Any]:
        async with self.db.session() as session:
            run = await session.get(AgentRun, ctx.run_id)
            config = dict(run.config) if run else {}
        config["remembered_approvals"] = ctx.remembered
        return config

    async def _finalise(
        self, approval_id: str, status: ApprovalStatus, resolution: dict[str, Any]
    ) -> ApprovalRequest | None:
        """Resolve a pending request exactly once. Returns None if it was not pending."""
        async with self.db.session() as session:
            result = await session.execute(
                update(ApprovalRequest)
                .where(
                    ApprovalRequest.id == approval_id,
                    ApprovalRequest.status == ApprovalStatus.PENDING,
                )
                .values(status=status, resolution=redact(resolution), resolved_at=utcnow())
            )
            await session.commit()
            if getattr(result, "rowcount", 0) != 1:
                return None
            row = await session.get(ApprovalRequest, approval_id, populate_existing=True)
            assert row is not None
        await self.services.bus.emit(
            EventInput(
                run_id=row.run_id,
                task_id=row.task_id,
                session_id=row.session_id,
                agent_id=row.session_id,
                type="approval.resolved",
                status=status,
                data={
                    "approval_id": row.id,
                    "kind": row.kind,
                    "tool_use_id": row.tool_use_id,
                    **resolution,
                },
            )
        )
        self.services.bus.notify("approval", row.id, action="resolved")
        return row

    async def resolve(
        self, approval_id: str, *, approve: bool, message: str | None, remember: bool
    ) -> ApprovalRequest:
        future = self._waiters.get(approval_id)
        async with self.db.session() as session:
            row = await session.get(ApprovalRequest, approval_id)
        if row is None:
            raise LookupError("Approval request not found")
        if row.status != ApprovalStatus.PENDING:
            raise ValueError(f"This request was already {row.status}")
        if row.kind == ApprovalKind.STEP:
            return await self.resolve_step(approval_id, approve=approve, message=message)
        if future is None or future.done():
            await self._finalise(
                approval_id,
                ApprovalStatus.EXPIRED,
                {"decision": "deny", "message": "The requesting agent is no longer running"},
            )
            raise ValueError("The agent that asked is no longer running; the request has expired")
        resolution = {
            "decision": "approve" if approve else "deny",
            "message": message,
            "remember": remember,
        }
        updated = await self._finalise(
            approval_id, ApprovalStatus.APPROVED if approve else ApprovalStatus.DENIED, resolution
        )
        if updated is None:
            async with self.db.session() as session:
                current = await session.get(ApprovalRequest, approval_id)
            raise ValueError(
                f"This request was already {current.status if current else 'resolved'}"
            )
        if not future.done():
            future.set_result(resolution)
        return updated

    async def expire_stale(self, reason: str) -> int:
        """Expire requests nobody can answer any more (workflow approval steps stay pending)."""
        async with self.db.session() as session:
            rows = (
                await session.scalars(
                    select(ApprovalRequest).where(
                        ApprovalRequest.status == ApprovalStatus.PENDING,
                        ApprovalRequest.kind != ApprovalKind.STEP,
                    )
                )
            ).all()
            ids = [r.id for r in rows if r.id not in self._waiters]
        for approval_id in ids:
            await self._finalise(
                approval_id, ApprovalStatus.EXPIRED, {"decision": "deny", "message": reason}
            )
        return len(ids)

    # -- workflow approval steps -----------------------------------------------------------
    async def pending_step(self, subtask_id: str) -> ApprovalRequest | None:
        async with self.db.session() as session:
            return await session.scalar(
                select(ApprovalRequest)
                .where(
                    ApprovalRequest.subtask_id == subtask_id,
                    ApprovalRequest.kind == ApprovalKind.STEP,
                    ApprovalRequest.status == ApprovalStatus.PENDING,
                )
                .order_by(ApprovalRequest.created_at.desc())
            )

    async def wait_step(self, ctx: RunContext, approval_id: str) -> str:
        """Wait for a decision on an approval step: "decided", "cancelled" or "stopped".

        Pausing or shutting down leaves the request pending for later ("stopped");
        cancelling the run cancels it. The decision itself is recorded by
        `resolve_step`, so there is nothing to apply here.
        """
        future = self._waiters.get(approval_id)
        if future is None or future.done():
            future = asyncio.get_running_loop().create_future()
            self._waiters[approval_id] = future
        stop_wait = asyncio.ensure_future(ctx.stop_event.wait())
        await self._enter_waiting(ctx, None)
        try:
            # Decided while the run was paused or interrupted, before this wait began.
            async with self.db.session() as session:
                row = await session.get(ApprovalRequest, approval_id)
            if row is None or row.status != ApprovalStatus.PENDING:
                return "decided"
            done, _ = await asyncio.wait({future, stop_wait}, return_when=asyncio.FIRST_COMPLETED)
            if future in done:
                return "decided"
            if ctx.stop_reason == "cancel":
                await self.cancel_steps(ctx.run_id, "The run was cancelled before anyone decided")
                return "cancelled"
            return "stopped"
        finally:
            stop_wait.cancel()
            self._waiters.pop(approval_id, None)
            await self._leave_waiting(ctx, None)

    async def resolve_step(
        self, approval_id: str, *, approve: bool, message: str | None
    ) -> ApprovalRequest:
        """Record a decision on an approval step, exactly once.

        The request and its step change in one transaction, and only from pending
        and waiting: a second click, another tab or a stale page gets an error
        instead of advancing the run twice.
        """
        note = redact((message or "").strip()) or None
        resolution = {"decision": "approve" if approve else "deny", "message": note}
        status = ApprovalStatus.APPROVED if approve else ApprovalStatus.DENIED
        async with self.db.session() as session:
            row = await session.get(ApprovalRequest, approval_id)
            if row is None or row.kind != ApprovalKind.STEP or not row.subtask_id:
                raise LookupError("Approval request not found")
            resolved = await session.execute(
                update(ApprovalRequest)
                .where(
                    ApprovalRequest.id == approval_id,
                    ApprovalRequest.status == ApprovalStatus.PENDING,
                )
                .values(status=status, resolution=resolution, resolved_at=utcnow())
            )
            if getattr(resolved, "rowcount", 0) != 1:
                await session.rollback()
                current = await session.get(ApprovalRequest, approval_id, populate_existing=True)
                raise ValueError(
                    f"This request was already {current.status if current else 'resolved'}"
                )
            if approve:
                fields = {
                    "status": SubtaskStatus.COMPLETED,
                    "report": f"Approved. Note from the approver: {note}" if note else "Approved.",
                    "error": None,
                }
            else:
                fields = {
                    "status": SubtaskStatus.REJECTED,
                    "error": f"Rejected: {note}" if note else "Rejected.",
                }
            stepped = await session.execute(
                update(Subtask)
                .where(
                    Subtask.id == row.subtask_id,
                    Subtask.status == SubtaskStatus.WAITING_APPROVAL,
                )
                .values(**fields)
            )
            if getattr(stepped, "rowcount", 0) != 1:
                await session.rollback()
                raise ValueError("This step isn't waiting for a decision any more")
            await session.commit()
            subtask_id = row.subtask_id
            row = await session.get(ApprovalRequest, approval_id, populate_existing=True)
            sub = await session.get(Subtask, subtask_id, populate_existing=True)
            assert row is not None and sub is not None
        await self.services.bus.emit(
            EventInput(
                run_id=row.run_id,
                task_id=row.task_id,
                type="approval.resolved",
                status=status,
                data={"approval_id": row.id, "kind": row.kind, "subtask_id": sub.id, **resolution},
            )
        )
        await self.services.bus.emit(
            subtask_event(
                row.run_id, row.task_id, sub, SubtaskStatus.WAITING_APPROVAL, by="approver"
            )
        )
        self.services.bus.notify("approval", row.id, action="resolved")
        self.services.bus.notify("run", row.run_id)
        future = self._waiters.get(approval_id)
        if future is not None and not future.done():
            future.set_result(resolution)
        return row

    async def cancel_steps(self, run_id: str, reason: str) -> int:
        """Cancel a run's pending approval steps (the run is being cancelled)."""
        async with self.db.session() as session:
            rows = (
                await session.scalars(
                    select(ApprovalRequest).where(
                        ApprovalRequest.run_id == run_id,
                        ApprovalRequest.kind == ApprovalKind.STEP,
                        ApprovalRequest.status == ApprovalStatus.PENDING,
                    )
                )
            ).all()
        count = 0
        for row in rows:
            if await self._finalise(
                row.id, ApprovalStatus.CANCELLED, {"decision": "deny", "message": reason}
            ):
                count += 1
            async with self.db.session() as session:
                sub = await session.get(Subtask, row.subtask_id) if row.subtask_id else None
                if sub is None or sub.status != SubtaskStatus.WAITING_APPROVAL:
                    continue
                sub.status, sub.error = SubtaskStatus.CANCELLED, f"Cancelled: {reason.lower()}."
                await session.commit()
            await self.services.bus.emit(
                subtask_event(run_id, row.task_id, sub, SubtaskStatus.WAITING_APPROVAL)
            )
        if count:
            self.services.bus.notify("run", run_id)
        return count
