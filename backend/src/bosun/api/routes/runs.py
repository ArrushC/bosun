from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bosun.api.deps import ServicesDep, SessionDep
from bosun.api.schemas import (
    ArtifactOut,
    DiffOut,
    EventOut,
    EventPage,
    IntegrateIn,
    MessageIn,
    RunListItem,
    RunOut,
    SessionOut,
    SubtaskAction,
    SubtaskOut,
)
from bosun.api.serialize import approvals_out, columns, run_list_items
from bosun.db.models import (
    AgentRun,
    AgentSession,
    ApprovalRequest,
    Artifact,
    Subtask,
    Task,
)
from bosun.orchestration.config import RunConfig
from bosun.services import Services

router = APIRouter(prefix="/runs", tags=["runs"])

_PUBLIC_CONFIG_KEYS = (
    "mode", "backend", "provider", "model", "effort", "single_role", "team_roles", "max_turns", "timeout_minutes",
    "require_plan_approval", "max_team_size", "max_parallel", "subtask_max_attempts", "allow_subagents",
    "allow_web_tools", "commit_agent_changes", "trusted_workspace", "instructions", "plan", "plan_warnings",
    "plan_approved", "remembered_approvals", "workflow",
)  # fmt: skip


async def run_detail(session: AsyncSession, services: Services, run_id: str) -> RunOut:
    run = await session.get(AgentRun, run_id)
    if run is None:
        raise HTTPException(404, "Run not found")
    task = await session.get(Task, run.task_id)
    sessions = (
        await session.scalars(
            select(AgentSession)
            .where(AgentSession.run_id == run_id)
            .order_by(AgentSession.created_at)
        )
    ).all()
    subtasks = (
        await session.scalars(
            select(Subtask).where(Subtask.run_id == run_id).order_by(Subtask.position)
        )
    ).all()
    artifacts = (
        await session.scalars(
            select(Artifact).where(Artifact.run_id == run_id).order_by(Artifact.created_at)
        )
    ).all()
    approvals = (
        await session.scalars(
            select(ApprovalRequest)
            .where(ApprovalRequest.run_id == run_id)
            .order_by(ApprovalRequest.created_at)
        )
    ).all()
    out = RunOut.model_validate(columns(run))
    out.task_key = task.key if task else None
    out.task_title = task.title if task else None
    ctx = services.runs.context(run_id)
    out.active = ctx is not None
    live = set(ctx.sessions) if ctx else set()
    out.sessions = [
        SessionOut.model_validate(s).model_copy(update={"live": s.id in live}) for s in sessions
    ]
    out.subtasks = [SubtaskOut.model_validate(s) for s in subtasks]
    out.artifacts = [ArtifactOut.model_validate(a) for a in artifacts]
    out.approvals = await approvals_out(session, approvals)
    config = {k: v for k, v in (run.config or {}).items() if k in _PUBLIC_CONFIG_KEYS}
    try:
        config["scope"] = RunConfig.model_validate(run.config).scope()
    except Exception:  # noqa: BLE001 - legacy/partial configs still render
        config["scope"] = {}
    out.config = config
    return out


@router.get("", response_model=list[RunListItem])
async def list_runs(
    session: SessionDep,
    status: str | None = None,
    task_id: str | None = None,
    workflow_id: str | None = None,
    limit: int = Query(default=200, ge=1, le=1000),
) -> list[RunListItem]:
    stmt = select(AgentRun)
    if status:
        stmt = stmt.where(AgentRun.status.in_(status.split(",")))
    if task_id:
        stmt = stmt.where(AgentRun.task_id == task_id)
    if workflow_id:
        stmt = stmt.where(AgentRun.workflow_id == workflow_id)
    rows = (await session.scalars(stmt.order_by(AgentRun.created_at.desc()).limit(limit))).all()
    return await run_list_items(session, rows)


@router.get("/{run_id}", response_model=RunOut)
async def get_run(run_id: str, session: SessionDep, services: ServicesDep) -> RunOut:
    return await run_detail(session, services, run_id)


@router.get("/{run_id}/events", response_model=EventPage)
async def events(
    run_id: str,
    services: ServicesDep,
    after_seq: int = Query(default=0, ge=0),
    limit: int = Query(default=2000, ge=1, le=10_000),
) -> EventPage:
    rows = await services.bus.history(run_id=run_id, after_seq=after_seq, limit=limit + 1)
    has_more = len(rows) > limit
    rows = rows[:limit]
    return EventPage(
        events=[EventOut(**r) for r in rows],
        last_seq=rows[-1]["seq"] if rows else after_seq,
        has_more=has_more,
    )


@router.get("/{run_id}/sessions/{session_id}/transcript")
async def transcript(run_id: str, session_id: str, services: ServicesDep) -> dict[str, Any]:
    """Observable conversation for one agent: messages, tool calls and results (no hidden reasoning)."""
    rows = await services.bus.history(run_id=run_id, limit=10_000)
    kinds = {
        "user.message",
        "assistant.text",
        "tool.call",
        "tool.result",
        "tool.denied",
        "session.turn_completed",
        "approval.requested",
        "approval.resolved",
        "agent.message",
        "subagent.spawned",
        "subagent.returned",
    }
    items = [r for r in rows if r["session_id"] == session_id and r["type"] in kinds]
    return {"session_id": session_id, "items": items}


@router.post("/{run_id}/cancel", status_code=202)
async def cancel(run_id: str, services: ServicesDep) -> dict[str, str]:
    await services.runs.cancel(run_id)
    return {"status": "cancelling"}


@router.post("/{run_id}/pause", status_code=202)
async def pause(run_id: str, services: ServicesDep) -> dict[str, str]:
    await services.runs.pause(run_id)
    return {"status": "pausing"}


@router.post("/{run_id}/resume", status_code=202)
async def resume(run_id: str, services: ServicesDep) -> dict[str, str]:
    await services.runs.resume(run_id)
    return {"status": "resuming"}


@router.post("/{run_id}/retry", status_code=202)
async def retry(run_id: str, services: ServicesDep) -> dict[str, str]:
    await services.runs.retry(run_id)
    return {"status": "retrying"}


@router.post("/{run_id}/messages", status_code=202)
async def message(run_id: str, body: MessageIn, services: ServicesDep) -> dict[str, str]:
    delivery = await services.runs.send_message(
        run_id, body.text, body.session_id, interrupt=body.interrupt
    )
    return {"delivery": delivery}


@router.post("/{run_id}/subtasks/{subtask_id}", status_code=202)
async def subtask_action(
    run_id: str, subtask_id: str, body: SubtaskAction, services: ServicesDep
) -> dict[str, str]:
    await services.runs.update_subtask(run_id, subtask_id, body.action)
    return {"status": "ok"}


@router.get("/{run_id}/diff", response_model=DiffOut)
async def diff(run_id: str, services: ServicesDep) -> DiffOut:
    return DiffOut(**await services.runs.diff(run_id))


@router.post("/{run_id}/integrate")
async def integrate(run_id: str, body: IntegrateIn, services: ServicesDep) -> dict[str, Any]:
    if not body.confirm:
        raise HTTPException(400, "Integration changes your repository; resend with confirm=true")
    return await services.runs.integrate(run_id, body.action)
