"""ORM → API schema conversion with batched lookups (no N+1 queries on the board)."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from bosun.api.schemas import (
    ApprovalOut,
    RunBrief,
    RunListItem,
    SourceRef,
    TaskOut,
    TaskRef,
)
from bosun.db.models import (
    AgentRun,
    AgentSession,
    ApprovalRequest,
    Subtask,
    Task,
    TaskDependency,
)
from bosun.domain.enums import (
    TERMINAL_SESSION_STATUSES,
    ApprovalStatus,
    SubtaskStatus,
    TaskStatus,
)


def columns(row: Any) -> dict[str, Any]:
    """Column values only — never touches (lazy) relationships."""
    return {c.key: getattr(row, c.key) for c in row.__table__.columns}


async def run_briefs(
    session: AsyncSession, runs: Sequence[AgentRun], live_ids: set[str] | None = None
) -> dict[str, RunBrief]:
    if not runs:
        return {}
    ids = [r.id for r in runs]
    sessions_by_run: dict[str, list[AgentSession]] = defaultdict(list)
    for row in (
        await session.scalars(
            select(AgentSession)
            .where(AgentSession.run_id.in_(ids))
            .order_by(AgentSession.created_at)
        )
    ).all():
        sessions_by_run[row.run_id].append(row)
    subtasks: dict[str, list[str]] = defaultdict(list)
    for run_id, status in (
        await session.execute(select(Subtask.run_id, Subtask.status).where(Subtask.run_id.in_(ids)))
    ).all():
        subtasks[run_id].append(status)
    approvals: dict[str, int] = dict(
        (
            await session.execute(
                select(ApprovalRequest.run_id, func.count(ApprovalRequest.id))
                .where(
                    ApprovalRequest.run_id.in_(ids),
                    ApprovalRequest.status == ApprovalStatus.PENDING,
                )
                .group_by(ApprovalRequest.run_id)
            )
        ).all()
    )
    briefs = {}
    for run in runs:
        rows = sessions_by_run[run.id]
        roles_seen: dict[str, dict[str, Any]] = {}
        for row in rows:
            entry = roles_seen.setdefault(
                row.role,
                {
                    "role": row.role,
                    "name": row.name.split(": ")[0],
                    "status": row.status,
                    "count": 0,
                },
            )
            entry["count"] += 1
            if row.status not in TERMINAL_SESSION_STATUSES:
                entry["status"] = row.status
        sub = subtasks[run.id]
        briefs[run.id] = RunBrief(
            id=run.id,
            number=run.number,
            status=run.status,
            mode=run.mode,
            strategy=run.strategy,
            backend=run.backend,
            started_at=run.started_at,
            ended_at=run.ended_at,
            error_kind=run.error_kind,
            sessions=len(rows),
            active_sessions=sum(1 for r in rows if r.status not in TERMINAL_SESSION_STATUSES),
            subtasks=len(sub),
            subtasks_done=sum(
                1 for s in sub if s in (SubtaskStatus.COMPLETED, SubtaskStatus.SKIPPED)
            ),
            pending_approvals=approvals.get(run.id, 0),
            agents=list(roles_seen.values()),
            integration_status=run.integration_status,
            workflow_name=((run.config or {}).get("workflow") or {}).get("name"),
        )
    return briefs


async def tasks_out(session: AsyncSession, tasks: Sequence[Task]) -> list[TaskOut]:
    if not tasks:
        return []
    ids = [t.id for t in tasks]
    deps: dict[str, list[str]] = defaultdict(list)
    for row in (
        await session.scalars(select(TaskDependency).where(TaskDependency.task_id.in_(ids)))
    ).all():
        deps[row.task_id].append(row.depends_on_id)
    dep_targets = {d for values in deps.values() for d in values}
    dep_status: dict[str, str] = {}
    if dep_targets:
        dep_status = dict(
            (
                await session.execute(select(Task.id, Task.status).where(Task.id.in_(dep_targets)))
            ).all()
        )

    latest_sub = (
        select(AgentRun.task_id, func.max(AgentRun.created_at).label("latest"))
        .where(AgentRun.task_id.in_(ids))
        .group_by(AgentRun.task_id)
        .subquery()
    )
    latest_runs = (
        await session.scalars(
            select(AgentRun).join(
                latest_sub,
                (AgentRun.task_id == latest_sub.c.task_id)
                & (AgentRun.created_at == latest_sub.c.latest),
            )
        )
    ).all()
    briefs = await run_briefs(session, latest_runs)
    latest_by_task = {r.task_id: briefs[r.id] for r in latest_runs}

    out = []
    for task in tasks:
        item = TaskOut.model_validate(task)
        item.key = task.key
        item.workspace_name = task.workspace.name if task.workspace else None
        item.sources = [SourceRef.model_validate(s) for s in task.sources]
        item.depends_on = deps.get(task.id, [])
        item.open_dependencies = sum(
            1 for d in item.depends_on if dep_status.get(d) != TaskStatus.DONE
        )
        item.latest_run = latest_by_task.get(task.id)
        out.append(item)
    return out


async def task_refs(session: AsyncSession, ids: list[str]) -> list[TaskRef]:
    if not ids:
        return []
    rows = (await session.scalars(select(Task).where(Task.id.in_(ids)))).all()
    return [TaskRef(id=t.id, key=t.key, title=t.title, status=t.status) for t in rows]


async def approvals_out(
    session: AsyncSession, rows: Sequence[ApprovalRequest]
) -> list[ApprovalOut]:
    task_ids = {r.task_id for r in rows if r.task_id}
    tasks = {}
    if task_ids:
        tasks = {
            t.id: t
            for t in (await session.scalars(select(Task).where(Task.id.in_(task_ids)))).all()
        }
    run_ids = {r.run_id for r in rows}
    statuses: dict[str, str] = {}
    if run_ids:
        found = await session.execute(
            select(AgentRun.id, AgentRun.status).where(AgentRun.id.in_(run_ids))
        )
        statuses = {run_id: status for run_id, status in found.tuples()}
    out = []
    for row in rows:
        item = ApprovalOut.model_validate(row)
        task = tasks.get(row.task_id or "")
        if task is not None:
            item.task_key, item.task_title = task.key, task.title
        item.run_status = statuses.get(row.run_id)
        out.append(item)
    return out


async def run_list_items(session: AsyncSession, runs: Sequence[AgentRun]) -> list[RunListItem]:
    if not runs:
        return []
    task_ids = {r.task_id for r in runs}
    tasks = {
        t.id: t for t in (await session.scalars(select(Task).where(Task.id.in_(task_ids)))).all()
    }
    roles: dict[str, list[str]] = defaultdict(list)
    counts: dict[str, int] = defaultdict(int)
    for run_id, role in (
        await session.execute(
            select(AgentSession.run_id, AgentSession.role).where(
                AgentSession.run_id.in_([r.id for r in runs])
            )
        )
    ).all():
        counts[run_id] += 1
        if role not in roles[run_id]:
            roles[run_id].append(role)
    items = []
    for run in runs:
        task = tasks.get(run.task_id)
        duration = None
        if run.started_at:
            end = run.ended_at
            if end is not None:
                duration = int((end - run.started_at).total_seconds() * 1000)
        items.append(
            RunListItem(
                id=run.id,
                task_id=run.task_id,
                task_key=task.key if task else "?",
                task_title=task.title if task else "(deleted task)",
                number=run.number,
                mode=run.mode,
                strategy=run.strategy,
                status=run.status,
                backend=run.backend,
                provider=run.provider,
                model=run.model,
                created_at=run.created_at,
                started_at=run.started_at,
                ended_at=run.ended_at,
                duration_ms=duration,
                sessions=counts[run.id],
                roles=roles[run.id],
                usage=run.usage or {},
                cost_usd=run.cost_usd,
                error_kind=run.error_kind,
                is_demo=run.is_demo,
                integration_status=run.integration_status,
                workflow_id=run.workflow_id,
                workflow_name=((run.config or {}).get("workflow") or {}).get("name"),
                workflow_version=run.workflow_version,
            )
        )
    return items
