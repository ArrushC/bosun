from __future__ import annotations

from fastapi import APIRouter, Header, HTTPException, Query
from sqlalchemy import or_, select
from sqlalchemy.orm import selectinload

from bosun.api.deps import ServicesDep, SessionDep
from bosun.api.routes.runs import run_detail
from bosun.api.schemas import (
    ActivityOut,
    CommentIn,
    DependencyIn,
    RunCreate,
    RunOut,
    RunPreview,
    TaskCreate,
    TaskDetail,
    TaskMove,
    TaskOut,
    TaskUpdate,
)
from bosun.api.serialize import run_briefs, task_refs, tasks_out
from bosun.db.models import AgentRun, Task, TaskActivity, TaskDependency, Workspace
from bosun.orchestration.config import RunRequest
from bosun.tasks.service import (
    TaskError,
    add_activity,
    add_dependency,
    create_task,
    load_task,
    move_task,
    remove_dependency,
    update_task,
)

router = APIRouter(prefix="/tasks", tags=["tasks"])


async def _get(session: SessionDep, task_id: str) -> Task:
    task = await load_task(session, task_id)
    if task is None:
        raise HTTPException(404, "Task not found")
    return task


@router.get("", response_model=list[TaskOut])
async def list_tasks(
    session: SessionDep,
    status: str | None = None,
    workspace_id: str | None = None,
    q: str | None = None,
    label: str | None = None,
    include_archived: bool = False,
    limit: int = Query(default=1000, ge=1, le=5000),
) -> list[TaskOut]:
    stmt = select(Task).options(selectinload(Task.sources), selectinload(Task.workspace))
    if not include_archived:
        stmt = stmt.where(Task.archived.is_(False))
    if status:
        stmt = stmt.where(Task.status.in_(status.split(",")))
    if workspace_id:
        stmt = stmt.where(Task.workspace_id == workspace_id)
    if q:
        like = f"%{q}%"
        stmt = stmt.where(or_(Task.title.ilike(like), Task.description.ilike(like)))
    rows = (await session.scalars(stmt.order_by(Task.status, Task.position).limit(limit))).all()
    out = await tasks_out(session, rows)
    if label:
        out = [t for t in out if label in t.labels]
    return out


@router.post("", response_model=TaskOut, status_code=201)
async def create(body: TaskCreate, session: SessionDep, services: ServicesDep) -> TaskOut:
    if body.workspace_id and await session.get(Workspace, body.workspace_id) is None:
        raise HTTPException(400, "Workspace not found")
    data = body.model_dump(exclude={"depends_on"})
    data["attachments"] = [a.model_dump() for a in body.attachments]
    task = await create_task(session, **data)
    for dep_id in body.depends_on:
        dep = await session.get(Task, dep_id)
        if dep is None:
            raise HTTPException(400, f"Dependency {dep_id} not found")
        try:
            await add_dependency(session, task, dep)
        except TaskError as exc:
            raise HTTPException(400, str(exc)) from exc
    await session.commit()
    services.bus.notify("task", task.id, action="created")
    return (await tasks_out(session, [await _get(session, task.id)]))[0]


@router.get("/labels", response_model=list[str])
async def labels(session: SessionDep) -> list[str]:
    found: set[str] = set()
    for row in (await session.scalars(select(Task.labels).where(Task.archived.is_(False)))).all():
        found.update(row or [])
    return sorted(found)


@router.get("/{task_id}", response_model=TaskDetail)
async def detail(task_id: str, session: SessionDep, services: ServicesDep) -> TaskDetail:
    task = await _get(session, task_id)
    base = (await tasks_out(session, [task]))[0]
    activity = (
        await session.scalars(
            select(TaskActivity)
            .where(TaskActivity.task_id == task_id)
            .order_by(TaskActivity.created_at.desc())
            .limit(200)
        )
    ).all()
    runs = (
        await session.scalars(
            select(AgentRun).where(AgentRun.task_id == task_id).order_by(AgentRun.created_at.desc())
        )
    ).all()
    briefs = await run_briefs(session, runs)
    dependents = (
        await session.scalars(
            select(TaskDependency.task_id).where(TaskDependency.depends_on_id == task_id)
        )
    ).all()
    return TaskDetail(
        **base.model_dump(),
        activity=[ActivityOut.model_validate(a) for a in activity],
        runs=[briefs[r.id] for r in runs],
        dependencies=await task_refs(session, base.depends_on),
        dependents=await task_refs(session, list(dependents)),
    )


@router.patch("/{task_id}", response_model=TaskOut)
async def update(
    task_id: str, body: TaskUpdate, session: SessionDep, services: ServicesDep
) -> TaskOut:
    task = await _get(session, task_id)
    patch = body.model_dump(exclude_unset=True)
    if "attachments" in patch and body.attachments is not None:
        patch["attachments"] = [a.model_dump() for a in body.attachments]
    if patch.get("workspace_id") and await session.get(Workspace, patch["workspace_id"]) is None:
        raise HTTPException(400, "Workspace not found")
    await update_task(session, task, patch)
    await session.commit()
    services.bus.notify("task", task_id)
    return (await tasks_out(session, [await _get(session, task_id)]))[0]


@router.post("/{task_id}/move", response_model=TaskOut)
async def move(task_id: str, body: TaskMove, session: SessionDep, services: ServicesDep) -> TaskOut:
    task = await _get(session, task_id)
    await move_task(session, task, body.status, before_id=body.before_id, after_id=body.after_id)
    await session.commit()
    services.bus.notify("task", task_id, action="moved")
    return (await tasks_out(session, [await _get(session, task_id)]))[0]


@router.delete("/{task_id}", status_code=204)
async def archive(task_id: str, session: SessionDep, services: ServicesDep) -> None:
    task = await _get(session, task_id)
    task.archived = True
    await add_activity(session, task, "archived", "Task archived")
    await session.commit()
    services.bus.notify("task", task_id, action="archived")


@router.post("/{task_id}/comments", response_model=ActivityOut, status_code=201)
async def comment(
    task_id: str, body: CommentIn, session: SessionDep, services: ServicesDep
) -> ActivityOut:
    task = await _get(session, task_id)
    activity = await add_activity(session, task, "comment", body.message)
    await session.commit()
    services.bus.notify("task", task_id)
    return ActivityOut.model_validate(activity)


@router.post("/{task_id}/dependencies", response_model=TaskOut)
async def add_dep(
    task_id: str, body: DependencyIn, session: SessionDep, services: ServicesDep
) -> TaskOut:
    task = await _get(session, task_id)
    dep = await session.get(Task, body.depends_on_id)
    if dep is None:
        raise HTTPException(404, "Dependency task not found")
    try:
        await add_dependency(session, task, dep)
    except TaskError as exc:
        raise HTTPException(400, str(exc)) from exc
    await session.commit()
    services.bus.notify("task", task_id)
    return (await tasks_out(session, [await _get(session, task_id)]))[0]


@router.delete("/{task_id}/dependencies/{depends_on_id}", response_model=TaskOut)
async def delete_dep(
    task_id: str, depends_on_id: str, session: SessionDep, services: ServicesDep
) -> TaskOut:
    task = await _get(session, task_id)
    await remove_dependency(session, task, depends_on_id)
    await session.commit()
    services.bus.notify("task", task_id)
    return (await tasks_out(session, [await _get(session, task_id)]))[0]


@router.post("/{task_id}/runs/preview", response_model=RunPreview)
async def preview_run(task_id: str, body: RunCreate, services: ServicesDep) -> RunPreview:
    return RunPreview(**await services.runs.preview(task_id, RunRequest(**body.model_dump())))


@router.post("/{task_id}/runs", response_model=RunOut, status_code=201)
async def start_run(
    task_id: str,
    body: RunCreate,
    services: ServicesDep,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key", max_length=200),
) -> RunOut:
    run = await services.runs.start(task_id, RunRequest(**body.model_dump()), idempotency_key)
    async with services.db.session() as session:
        return await run_detail(session, services, run.id)
