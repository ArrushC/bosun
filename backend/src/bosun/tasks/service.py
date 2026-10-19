"""Task management: creation, updates, board moves, dependencies, activity and import."""

from __future__ import annotations

import re
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from bosun.db.models import (
    AgentRun,
    SourceRecord,
    Task,
    TaskActivity,
    TaskDependency,
    TaskSourceLink,
)
from bosun.domain.enums import (
    ACTIVE_RUN_STATUSES,
    ExecutionMode,
    SourceKind,
    TaskKind,
    TaskPriority,
    TaskStatus,
)
from bosun.domain.state_machines import InvalidTransition, ensure_task_transition
from bosun.ids import utcnow

POSITION_STEP = 1024.0


class TaskError(ValueError):
    pass


async def next_task_number(session: AsyncSession) -> int:
    current = await session.scalar(select(func.max(Task.number)))
    return (current or 0) + 1


async def next_position(session: AsyncSession, status: str) -> float:
    current = await session.scalar(
        select(func.min(Task.position)).where(Task.status == status, Task.archived.is_(False))
    )
    return (current if current is not None else POSITION_STEP) - POSITION_STEP


async def add_activity(
    session: AsyncSession,
    task: Task,
    kind: str,
    message: str,
    *,
    actor: str = "human",
    data: dict[str, Any] | None = None,
    run_id: str | None = None,
) -> TaskActivity:
    activity = TaskActivity(
        task_id=task.id, kind=kind, actor=actor, message=message, data=data or {}, run_id=run_id
    )
    session.add(activity)
    task.last_activity_at = utcnow()
    task.last_activity = message
    return activity


async def has_active_run(session: AsyncSession, task_id: str) -> bool:
    found = await session.scalar(
        select(AgentRun.id).where(
            AgentRun.task_id == task_id, AgentRun.status.in_(list(ACTIVE_RUN_STATUSES))
        )
    )
    return found is not None


async def create_task(
    session: AsyncSession, *, actor: str = "human", is_demo: bool = False, **fields: Any
) -> Task:
    status = fields.pop("status", TaskStatus.BACKLOG)
    task = Task(
        number=await next_task_number(session),
        status=status,
        position=fields.pop("position", None) or await next_position(session, status),
        is_demo=is_demo,
        **fields,
    )
    session.add(task)
    await session.flush()
    await add_activity(
        session,
        task,
        "created",
        f"Task created in {TaskStatus(status).value.replace('_', ' ')}",
        actor=actor,
    )
    return task


async def update_task(
    session: AsyncSession, task: Task, patch: dict[str, Any], *, actor: str = "human"
) -> Task:
    changed = []
    for field, value in patch.items():
        if field in ("status", "position"):
            continue
        if getattr(task, field) != value:
            setattr(task, field, value)
            changed.append(field)
    if "status" in patch and patch["status"] != task.status:
        await move_task(session, task, patch["status"], actor=actor)
    if changed:
        await add_activity(
            session,
            task,
            "updated",
            f"Updated {', '.join(f.replace('_', ' ') for f in changed)}",
            actor=actor,
        )
    return task


async def move_task(
    session: AsyncSession,
    task: Task,
    status: str,
    *,
    actor: str = "human",
    before_id: str | None = None,
    after_id: str | None = None,
    message: str | None = None,
    run_id: str | None = None,
) -> Task:
    target = TaskStatus(status)
    current = TaskStatus(task.status)
    if target != current:
        ensure_task_transition(
            current, target, actor=actor, has_active_run=await has_active_run(session, task.id)
        )
    # Ordering within the column: place between neighbours (fractional positions).
    if before_id or after_id:
        before = await session.get(Task, before_id) if before_id else None
        after = await session.get(Task, after_id) if after_id else None
        if before and after:
            task.position = (before.position + after.position) / 2
        elif after:  # dropped below `after`
            task.position = after.position + POSITION_STEP
        elif before:  # dropped above `before`
            task.position = before.position - POSITION_STEP
    elif target != current:
        task.position = await next_position(session, target)

    if target != current:
        task.status = target
        task.completed_at = utcnow() if target == TaskStatus.DONE else None
        label = target.value.replace("_", " ")
        await add_activity(
            session,
            task,
            "status",
            message or f"Moved to {label}",
            actor=actor,
            data={"from": current.value, "to": target.value},
            run_id=run_id,
        )
    return task


async def system_transition(
    session: AsyncSession,
    task_id: str,
    target: TaskStatus,
    message: str,
    *,
    run_id: str | None = None,
) -> bool:
    """Agent-driven status change. Never raises: disallowed moves are recorded as activity only."""
    task = await session.get(Task, task_id)
    if task is None:
        return False
    if task.status == target:
        await add_activity(session, task, "run", message, actor="system", run_id=run_id)
        return True
    try:
        ensure_task_transition(TaskStatus(task.status), target, actor="system")
    except InvalidTransition:
        await add_activity(session, task, "run", message, actor="system", run_id=run_id)
        return False
    await move_task(session, task, target, actor="system", message=message, run_id=run_id)
    return True


# -- dependencies -------------------------------------------------------------
async def _dependency_graph(session: AsyncSession) -> dict[str, set[str]]:
    graph: dict[str, set[str]] = {}
    for dep in (await session.scalars(select(TaskDependency))).all():
        graph.setdefault(dep.task_id, set()).add(dep.depends_on_id)
    return graph


async def add_dependency(session: AsyncSession, task: Task, depends_on: Task) -> None:
    if task.id == depends_on.id:
        raise TaskError("A task cannot depend on itself")
    graph = await _dependency_graph(session)
    # Adding task -> depends_on creates a cycle if depends_on already reaches task.
    stack, seen = [depends_on.id], set()
    while stack:
        node = stack.pop()
        if node == task.id:
            raise TaskError(
                f"{depends_on.key} already depends on {task.key}; this would create a cycle"
            )
        if node in seen:
            continue
        seen.add(node)
        stack.extend(graph.get(node, ()))
    if task.id in graph and depends_on.id in graph[task.id]:
        return
    session.add(TaskDependency(task_id=task.id, depends_on_id=depends_on.id))
    await add_activity(session, task, "dependency", f"Now depends on {depends_on.key}")


async def remove_dependency(session: AsyncSession, task: Task, depends_on_id: str) -> None:
    dep = await session.get(TaskDependency, {"task_id": task.id, "depends_on_id": depends_on_id})
    if dep is not None:
        await session.delete(dep)
        await add_activity(session, task, "dependency", "Removed a dependency")


# -- import from discovered sources --------------------------------------------
_KIND_FROM_TAG = {
    "FIXME": TaskKind.BUGFIX,
    "BUG": TaskKind.BUGFIX,
    "HACK": TaskKind.REFACTOR,
    "XXX": TaskKind.REFACTOR,
}
_PRIORITY_FROM_TAG = {"FIXME": TaskPriority.HIGH, "BUG": TaskPriority.HIGH}
_BUG_LABEL = re.compile(r"(?i)\b(bug|defect|regression|crash)\b")


def _suggest(record: SourceRecord) -> dict[str, Any]:
    if record.kind == SourceKind.GITHUB_PR:
        kind = TaskKind.REVIEW
    elif record.kind == SourceKind.GITHUB_ISSUE:
        kind = (
            TaskKind.BUGFIX
            if any(_BUG_LABEL.search(lbl) for lbl in record.labels)
            else TaskKind.IMPLEMENTATION
        )
    else:
        kind = _KIND_FROM_TAG.get((record.tag or "").upper(), TaskKind.IMPLEMENTATION)
    priority = _PRIORITY_FROM_TAG.get((record.tag or "").upper(), TaskPriority.NONE)
    labels = (
        list(record.labels)
        if record.kind in (SourceKind.GITHUB_ISSUE, SourceKind.GITHUB_PR)
        else []
    )
    if record.tag:
        labels.append(record.tag.lower())
    return {"kind": kind, "priority": priority, "labels": labels}


def _description(record: SourceRecord) -> str:
    if record.kind in (SourceKind.GITHUB_ISSUE, SourceKind.GITHUB_PR):
        what = "Pull request" if record.kind == SourceKind.GITHUB_PR else "Issue"
        header = f"{what} [{record.repo}#{record.number}]({record.url}) by @{record.author}"
        body = record.body or "_No description provided._"
        return f"{header}\n\n{body}"
    location = f"`{record.path}:{record.line}`"
    section = f" (section: {record.meta.get('section')})" if record.meta.get("section") else ""
    context = f"\n\n```\n{record.context}\n```" if record.context else ""
    return f"Discovered at {location}{section}.\n\n> {record.body or record.title}{context}"


def _source_label(record: SourceRecord) -> str:
    if record.kind == SourceKind.GITHUB_ISSUE:
        return f"issue {record.repo}#{record.number}"
    if record.kind == SourceKind.GITHUB_PR:
        return f"pull request {record.repo}#{record.number}"
    what = {
        SourceKind.CODE_TODO: f"{record.tag or 'TODO'} comment",
        SourceKind.TODO_FILE: "TODO file",
    }.get(SourceKind(record.kind), "Markdown task")
    return f"{what} in {record.path}:{record.line}"


async def import_records(
    session: AsyncSession,
    records: list[SourceRecord],
    *,
    status: str = TaskStatus.BACKLOG,
    workspace_id: str | None = None,
    execution_mode: str = ExecutionMode.SINGLE,
    actor: str = "human",
) -> list[Task]:
    tasks: list[Task] = []
    for record in records:
        existing = await session.scalar(
            select(Task)
            .join(TaskSourceLink, TaskSourceLink.task_id == Task.id)
            .where(TaskSourceLink.source_record_id == record.id, Task.archived.is_(False))
        )
        if existing is not None:
            tasks.append(existing)
            continue
        suggestion = _suggest(record)
        task = await create_task(
            session,
            actor=actor,
            is_demo=record.is_demo,
            title=record.title,
            description=_description(record),
            status=status,
            workspace_id=record.workspace_id or workspace_id,
            execution_mode=execution_mode,
            **suggestion,
        )
        session.add(TaskSourceLink(task_id=task.id, source_record_id=record.id))
        await add_activity(
            session, task, "source", f"Imported from {_source_label(record)}", actor=actor
        )
        tasks.append(task)
    await session.flush()
    return tasks


async def load_task(session: AsyncSession, task_id: str) -> Task | None:
    return await session.scalar(
        select(Task)
        .options(selectinload(Task.sources), selectinload(Task.workspace))
        .where(Task.id == task_id)
    )
