from __future__ import annotations

from collections import defaultdict
from datetime import timedelta
from typing import Any

from fastapi import APIRouter
from sqlalchemy import func, select

from bosun.api.deps import SessionDep
from bosun.api.routes.settings import _latest_usage
from bosun.api.routes.workspaces import _out as workspaces_out
from bosun.api.schemas import DashboardOut, OverviewGraph
from bosun.api.serialize import approvals_out, run_list_items
from bosun.db.models import (
    AgentRun,
    AgentSession,
    ApprovalRequest,
    SourceConnection,
    SourceRecord,
    Task,
    TaskActivity,
    TaskDependency,
    TaskSourceLink,
    Workspace,
)
from bosun.domain.enums import (
    ACTIVE_RUN_STATUSES,
    ApprovalStatus,
    RunStatus,
    SourceRecordState,
    TaskStatus,
)
from bosun.ids import utcnow

router = APIRouter(tags=["dashboard"])


@router.get("/dashboard", response_model=DashboardOut)
async def dashboard(session: SessionDep) -> DashboardOut:
    tasks_by_status = {s.value: 0 for s in TaskStatus}
    for status, count in (
        await session.execute(
            select(Task.status, func.count(Task.id))
            .where(Task.archived.is_(False))
            .group_by(Task.status)
        )
    ).all():
        tasks_by_status[status] = count
    runs_by_status: dict[str, int] = dict(
        (
            await session.execute(
                select(AgentRun.status, func.count(AgentRun.id)).group_by(AgentRun.status)
            )
        ).all()
    )
    linked = select(TaskSourceLink.source_record_id)
    untriaged = await session.scalar(
        select(func.count(SourceRecord.id)).where(
            SourceRecord.state == SourceRecordState.OPEN,
            SourceRecord.dismissed.is_(False),
            SourceRecord.id.not_in(linked),
        )
    )
    active = (
        await session.scalars(
            select(AgentRun)
            .where(AgentRun.status.in_(list(ACTIVE_RUN_STATUSES)))
            .order_by(AgentRun.created_at.desc())
        )
    ).all()
    recent = (
        await session.scalars(
            select(AgentRun)
            .where(AgentRun.status.not_in(list(ACTIVE_RUN_STATUSES)))
            .order_by(AgentRun.updated_at.desc())
            .limit(8)
        )
    ).all()
    approvals = (
        await session.scalars(
            select(ApprovalRequest)
            .where(ApprovalRequest.status == ApprovalStatus.PENDING)
            .order_by(ApprovalRequest.created_at)
        )
    ).all()
    workspaces = list(
        (await session.scalars(select(Workspace).order_by(Workspace.created_at))).all()
    )
    active_sessions = await session.scalar(
        select(func.count(AgentSession.id)).where(
            AgentSession.status.in_(["running", "waiting_approval"])
        )
    )
    activity_rows = (
        await session.execute(
            select(TaskActivity, Task.number, Task.title)
            .join(Task, Task.id == TaskActivity.task_id)
            .order_by(TaskActivity.created_at.desc())
            .limit(25)
        )
    ).all()
    activity = [
        {
            "id": a.id,
            "task_id": a.task_id,
            "task_key": f"BOS-{number}",
            "task_title": title,
            "kind": a.kind,
            "actor": a.actor,
            "message": a.message,
            "run_id": a.run_id,
            "created_at": a.created_at.isoformat(),
        }
        for a, number, title in activity_rows
    ]

    since = utcnow() - timedelta(days=13)
    day_keys = [(since + timedelta(days=i)).date().isoformat() for i in range(14)]
    buckets: dict[str, dict[str, Any]] = {
        d: {"date": d, "completed": 0, "failed": 0, "done": 0} for d in day_keys
    }
    for status, ended in (
        await session.execute(
            select(AgentRun.status, AgentRun.ended_at).where(AgentRun.ended_at >= since)
        )
    ).all():
        if ended is None:
            continue
        key = ended.date().isoformat()
        if key in buckets and status in (RunStatus.COMPLETED, RunStatus.FAILED):
            buckets[key][status] += 1
    for (completed,) in (
        await session.execute(select(Task.completed_at).where(Task.completed_at >= since))
    ).all():
        if completed is None:
            continue
        key = completed.date().isoformat()
        if key in buckets:
            buckets[key]["done"] += 1

    usage: dict[str, Any] = defaultdict(int)
    week = utcnow() - timedelta(days=7)
    for (run_usage,) in (
        await session.execute(select(AgentRun.usage).where(AgentRun.created_at >= week))
    ).all():
        for key in (
            "input_tokens",
            "output_tokens",
            "cache_read_input_tokens",
            "cache_creation_input_tokens",
            "num_turns",
        ):
            value = (run_usage or {}).get(key)
            if isinstance(value, int):
                usage[key] += value
    usage_out: dict[str, Any] = dict(usage)
    usage_out.update(await _latest_usage(session))

    return DashboardOut(
        counts={
            "tasks": sum(tasks_by_status.values()),
            "active_runs": len(active),
            "active_agents": active_sessions or 0,
            "pending_approvals": len(approvals),
            "untriaged": untriaged or 0,
            "workspaces": len(workspaces),
            "connections": await session.scalar(select(func.count(SourceConnection.id))) or 0,
            "blocked": tasks_by_status.get(TaskStatus.BLOCKED, 0),
            "review": tasks_by_status.get(TaskStatus.REVIEW, 0),
        },
        tasks_by_status=tasks_by_status,
        runs_by_status=runs_by_status,
        active_runs=await run_list_items(session, active),
        recent_runs=await run_list_items(session, recent),
        pending_approvals=await approvals_out(session, approvals),
        workspaces=await workspaces_out(session, workspaces),
        activity=activity,
        throughput=list(buckets.values()),
        usage=usage_out,
    )


@router.get("/graph/overview", response_model=OverviewGraph)
async def overview_graph(session: SessionDep, days: int = 7) -> OverviewGraph:
    """Global view: tasks, their dependencies, recent runs and the agents in them."""
    cutoff = utcnow() - timedelta(days=days)
    runs = (
        await session.scalars(
            select(AgentRun)
            .where(
                (AgentRun.status.in_(list(ACTIVE_RUN_STATUSES))) | (AgentRun.updated_at >= cutoff)
            )
            .order_by(AgentRun.created_at.desc())
            .limit(60)
        )
    ).all()
    latest_by_task: dict[str, AgentRun] = {}
    for run in runs:
        latest_by_task.setdefault(run.task_id, run)
    deps = (await session.scalars(select(TaskDependency))).all()
    task_ids = set(latest_by_task) | {d.task_id for d in deps} | {d.depends_on_id for d in deps}
    tasks = (
        (
            await session.scalars(
                select(Task).where(Task.id.in_(task_ids), Task.archived.is_(False))
            )
        ).all()
        if task_ids
        else []
    )
    sessions = (
        (
            await session.scalars(
                select(AgentSession).where(
                    AgentSession.run_id.in_([r.id for r in latest_by_task.values()])
                )
            )
        ).all()
        if latest_by_task
        else []
    )

    nodes: list[dict[str, Any]] = []
    edges: list[dict[str, Any]] = []
    visible = {t.id for t in tasks}
    for task in tasks:
        nodes.append(
            {
                "id": task.id,
                "type": "task",
                "data": {
                    "key": task.key,
                    "title": task.title,
                    "status": task.status,
                    "priority": task.priority,
                },
            }
        )
    for dep in deps:
        if dep.task_id in visible and dep.depends_on_id in visible:
            edges.append(
                {
                    "id": f"dep:{dep.task_id}:{dep.depends_on_id}",
                    "source": dep.depends_on_id,
                    "target": dep.task_id,
                    "kind": "depends",
                }
            )
    for task_id, run in latest_by_task.items():
        if task_id not in visible:
            continue
        nodes.append(
            {
                "id": run.id,
                "type": "run",
                "data": {
                    "number": run.number,
                    "status": run.status,
                    "mode": run.mode,
                    "strategy": run.strategy,
                    "task_id": task_id,
                },
            }
        )
        edges.append(
            {"id": f"run:{run.id}", "source": task_id, "target": run.id, "kind": "assigned"}
        )
    for s in sessions:
        nodes.append(
            {
                "id": s.id,
                "type": "agent",
                "data": {
                    "name": s.name,
                    "role": s.role,
                    "status": s.status,
                    "kind": s.kind,
                    "run_id": s.run_id,
                },
            }
        )
        source = s.parent_session_id or s.run_id
        edges.append(
            {
                "id": f"ses:{s.id}",
                "source": source,
                "target": s.id,
                "kind": "delegated" if s.parent_session_id else "assigned",
            }
        )
    return OverviewGraph(nodes=nodes, edges=edges)
