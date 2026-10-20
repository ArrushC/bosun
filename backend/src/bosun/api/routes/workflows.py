"""Authored workflows: drafts that run on board tasks through the team engine."""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Header, HTTPException, Response
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from bosun.api.deps import ServicesDep, SessionDep
from bosun.api.routes.runs import run_detail
from bosun.api.schemas import (
    NewTaskIn,
    RunOut,
    WorkflowCreate,
    WorkflowListItem,
    WorkflowOut,
    WorkflowRunBrief,
    WorkflowRunCreate,
    WorkflowUpdate,
)
from bosun.db.models import AgentRun, Task, Workflow, Workspace
from bosun.domain.enums import TaskStatus
from bosun.orchestration.config import RunRequest
from bosun.services import Services
from bosun.tasks.service import create_task
from bosun.workflows.service import check, describe_errors, spec_of
from bosun.workflows.validation import errors

router = APIRouter(prefix="/workflows", tags=["workflows"])


async def _get(session: AsyncSession, workflow_id: str) -> Workflow:
    row = await session.get(Workflow, workflow_id)
    if row is None or row.archived:
        raise HTTPException(404, "Workflow not found")
    return row


async def _out(session: AsyncSession, row: Workflow) -> WorkflowOut:
    return WorkflowOut(
        id=row.id,
        name=row.name,
        description=row.description,
        spec=spec_of(row),
        revision=row.revision,
        created_at=row.created_at,
        updated_at=row.updated_at,
        issues=await check(session, row),
    )


@router.get("", response_model=list[WorkflowListItem])
async def list_workflows(session: SessionDep) -> list[WorkflowListItem]:
    rows = (
        await session.scalars(
            select(Workflow)
            .where(Workflow.archived.is_(False))
            .order_by(Workflow.updated_at.desc())
        )
    ).all()
    totals = await session.execute(
        select(AgentRun.workflow_id, func.count())
        .where(AgentRun.workflow_id.in_([r.id for r in rows]))
        .group_by(AgentRun.workflow_id)
    )
    counts = {workflow_id: n for workflow_id, n in totals.tuples()}
    out = []
    for row in rows:
        last = None
        if counts.get(row.id):
            run = await session.scalar(
                select(AgentRun)
                .where(AgentRun.workflow_id == row.id)
                .order_by(AgentRun.created_at.desc())
            )
            task = await session.get(Task, run.task_id) if run else None
            if run is not None:
                last = WorkflowRunBrief(
                    id=run.id,
                    number=run.number,
                    status=run.status,
                    task_id=run.task_id,
                    task_key=task.key if task else "?",
                    created_at=run.created_at,
                )
        out.append(
            WorkflowListItem(
                id=row.id,
                name=row.name,
                description=row.description,
                steps=len(spec_of(row).steps),
                revision=row.revision,
                updated_at=row.updated_at,
                run_count=counts.get(row.id, 0),
                last_run=last,
            )
        )
    return out


@router.post("", response_model=WorkflowOut, status_code=201)
async def create_workflow(
    body: WorkflowCreate, session: SessionDep, services: ServicesDep
) -> WorkflowOut:
    row = Workflow(
        name=body.name.strip(),
        description=body.description.strip(),
        spec=body.spec.model_dump(mode="json"),
    )
    session.add(row)
    await session.commit()
    services.bus.notify("workflow", row.id, action="created")
    return await _out(session, row)


@router.get("/{workflow_id}", response_model=WorkflowOut)
async def get_workflow(workflow_id: str, session: SessionDep) -> WorkflowOut:
    return await _out(session, await _get(session, workflow_id))


@router.patch("/{workflow_id}", response_model=WorkflowOut)
async def update_workflow(
    workflow_id: str, body: WorkflowUpdate, session: SessionDep, services: ServicesDep
) -> WorkflowOut:
    row = await _get(session, workflow_id)
    if body.revision != row.revision:
        raise HTTPException(
            409,
            "This workflow was changed somewhere else since you opened it. "
            "Reload to see the latest version.",
        )
    if body.name is not None:
        row.name = body.name.strip()
    if body.description is not None:
        row.description = body.description.strip()
    if body.spec is not None:
        row.spec = body.spec.model_dump(mode="json")
    row.revision += 1
    await session.commit()
    services.bus.notify("workflow", row.id)
    return await _out(session, row)


@router.delete("/{workflow_id}", status_code=204)
async def delete_workflow(workflow_id: str, session: SessionDep, services: ServicesDep) -> Response:
    # Archived, not deleted: runs keep linking to the workflow they executed.
    row = await _get(session, workflow_id)
    row.archived = True
    await session.commit()
    services.bus.notify("workflow", row.id, action="deleted")
    return Response(status_code=204)


@router.post("/{workflow_id}/runs", response_model=RunOut, status_code=201)
async def run_workflow(
    workflow_id: str,
    body: WorkflowRunCreate,
    services: ServicesDep,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key", max_length=200),
) -> RunOut:
    """Run the current draft on an existing task, or on a new task created for it."""
    async with services.db.session() as session:
        if idempotency_key:
            existing = await session.scalar(
                select(AgentRun).where(AgentRun.idempotency_key == idempotency_key)
            )
            if existing is not None:
                return await run_detail(session, services, existing.id)
        row = await _get(session, workflow_id)
        if body.revision != row.revision:
            raise HTTPException(
                409,
                "This workflow changed after you chose to run it. Check the latest version and run it again.",
            )
        issues = await check(session, row)
        if errors(issues):
            raise HTTPException(422, describe_errors(issues, spec_of(row)))
        new_task_id = None
        if body.new_task is not None:
            new_task_id = await _create_task_for_run(session, services, body.new_task)
            task_id = new_task_id
        else:
            assert body.task_id is not None
            if await session.get(Task, body.task_id) is None:
                raise HTTPException(404, "Task not found")
            task_id = body.task_id
    try:
        run = await services.runs.start(
            task_id, RunRequest(workflow_id=workflow_id), idempotency_key
        )
    except Exception:
        if new_task_id is not None:
            # Don't leave a task behind for a run that never started.
            async with services.db.session() as session:
                task = await session.get(Task, new_task_id)
                if task is not None:
                    await session.delete(task)
                    await session.commit()
            services.bus.notify("task", new_task_id, action="deleted")
        raise
    async with services.db.session() as session:
        return await run_detail(session, services, run.id)


async def _create_task_for_run(session: AsyncSession, services: Services, new: NewTaskIn) -> str:
    workspace = await session.get(Workspace, new.workspace_id)
    if workspace is None:
        raise HTTPException(404, "Workspace not found")
    if not Path(workspace.path).is_dir():
        raise HTTPException(409, f"Workspace directory {workspace.path} no longer exists")
    task = await create_task(
        session,
        title=new.title.strip(),
        description=new.description.strip(),
        workspace_id=workspace.id,
        status=TaskStatus.READY,
    )
    await session.commit()
    services.bus.notify("task", task.id, action="created")
    return task.id
