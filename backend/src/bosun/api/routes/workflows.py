"""Authored workflows: drafts, published versions and templates, run on board tasks."""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Header, HTTPException, Response
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from bosun.api.deps import ServicesDep, SessionDep
from bosun.api.routes.runs import run_detail
from bosun.api.schemas import (
    NewTaskIn,
    RunOut,
    TemplateOut,
    VersionAgent,
    WorkflowCreate,
    WorkflowListItem,
    WorkflowOut,
    WorkflowPublish,
    WorkflowRestore,
    WorkflowRunBrief,
    WorkflowRunCreate,
    WorkflowUpdate,
    WorkflowVersionBrief,
    WorkflowVersionOut,
)
from bosun.db.models import AgentRun, Task, Workflow, WorkflowVersion, Workspace
from bosun.domain.enums import TaskStatus
from bosun.orchestration.config import RoleConfig, RunRequest
from bosun.services import Services
from bosun.settings_store import load_settings
from bosun.tasks.service import create_task
from bosun.workflows import service as wf
from bosun.workflows.spec import WorkflowSpec
from bosun.workflows.templates import TEMPLATES, setup, template
from bosun.workflows.validation import errors

router = APIRouter(prefix="/workflows", tags=["workflows"])
templates_router = APIRouter(prefix="/workflow-templates", tags=["workflows"])

CHANGED_ELSEWHERE = (
    "This workflow was changed somewhere else since you opened it. "
    "Reload to see the latest version."
)


async def _get(session: AsyncSession, workflow_id: str) -> Workflow:
    row = await session.get(Workflow, workflow_id)
    if row is None or row.archived:
        raise HTTPException(404, "Workflow not found")
    return row


async def _version_runs(session: AsyncSession, workflow_id: str) -> dict[int, int]:
    rows = await session.execute(
        select(AgentRun.workflow_version, func.count())
        .where(AgentRun.workflow_id == workflow_id, AgentRun.workflow_version.is_not(None))
        .group_by(AgentRun.workflow_version)
    )
    return {number: n for number, n in rows.tuples() if number is not None}


def _brief(row: WorkflowVersion, runs: dict[int, int]) -> WorkflowVersionBrief:
    return WorkflowVersionBrief(
        number=row.number,
        name=row.name,
        note=row.note,
        steps=len(wf.spec_of(row).steps),
        created_at=row.created_at,
        draft_revision=row.draft_revision,
        run_count=runs.get(row.number, 0),
    )


async def _latest_brief(
    session: AsyncSession, row: Workflow
) -> tuple[WorkflowVersion | None, WorkflowVersionBrief | None]:
    latest = await wf.latest_version(session, row.id)
    if latest is None:
        return None, None
    return latest, _brief(latest, await _version_runs(session, row.id))


async def _out(session: AsyncSession, row: Workflow) -> WorkflowOut:
    latest, brief = await _latest_brief(session, row)
    return WorkflowOut(
        id=row.id,
        name=row.name,
        description=row.description,
        spec=wf.spec_of(row),
        revision=row.revision,
        created_at=row.created_at,
        updated_at=row.updated_at,
        issues=await wf.check(session, row),
        template=row.template,
        latest_version=brief,
        unpublished_changes=not wf.matches(row, latest),
    )


async def _version_out(
    session: AsyncSession, workflow: Workflow, row: WorkflowVersion
) -> WorkflowVersionOut:
    changes = await wf.agent_changes(session, row)
    latest = await wf.latest_version(session, workflow.id)
    spec = wf.spec_of(row)
    agents = []
    for data in row.agents.values():
        agent = RoleConfig.model_validate(data)
        agents.append(
            VersionAgent(
                key=agent.key,
                name=agent.name,
                permission_profile=agent.permission_profile,
                model=agent.model,
                color=agent.color,
            )
        )
    return WorkflowVersionOut(
        **_brief(row, await _version_runs(session, workflow.id)).model_dump(),
        workflow_id=workflow.id,
        description=row.description,
        spec=spec,
        agents=agents,
        agent_changes=changes,
        issues=wf.version_issues(changes, spec),
        is_latest=latest is not None and latest.id == row.id,
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
        latest, brief = await _latest_brief(session, row)
        out.append(
            WorkflowListItem(
                id=row.id,
                name=row.name,
                description=row.description,
                steps=len(wf.spec_of(row).steps),
                revision=row.revision,
                updated_at=row.updated_at,
                run_count=counts.get(row.id, 0),
                last_run=last,
                template=row.template,
                latest_version=brief,
                unpublished_changes=not wf.matches(row, latest),
            )
        )
    return out


@router.post("", response_model=WorkflowOut, status_code=201)
async def create_workflow(
    body: WorkflowCreate, session: SessionDep, services: ServicesDep
) -> WorkflowOut:
    spec, description = body.spec, body.description.strip()
    if body.template is not None:
        source = template(body.template)
        if source is None:
            raise HTTPException(404, "Template not found")
        # A copy: the new draft and the template never affect each other.
        spec = WorkflowSpec(steps=[s.model_copy(deep=True) for s in source.steps])
        description = description or source.description
    row = Workflow(
        name=body.name.strip(),
        description=description,
        spec=spec.model_dump(mode="json"),
        template=body.template,
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
        raise HTTPException(409, CHANGED_ELSEWHERE)
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
    # Archived, not deleted: runs keep linking to the workflow and versions they executed.
    row = await _get(session, workflow_id)
    row.archived = True
    await session.commit()
    services.bus.notify("workflow", row.id, action="deleted")
    return Response(status_code=204)


# -- versions ---------------------------------------------------------------------------------
@router.get("/{workflow_id}/versions", response_model=list[WorkflowVersionBrief])
async def list_versions(workflow_id: str, session: SessionDep) -> list[WorkflowVersionBrief]:
    await _get(session, workflow_id)
    rows = (
        await session.scalars(
            select(WorkflowVersion)
            .where(WorkflowVersion.workflow_id == workflow_id)
            .order_by(WorkflowVersion.number.desc())
        )
    ).all()
    runs = await _version_runs(session, workflow_id)
    return [_brief(r, runs) for r in rows]


@router.get("/{workflow_id}/versions/{number}", response_model=WorkflowVersionOut)
async def get_version(workflow_id: str, number: int, session: SessionDep) -> WorkflowVersionOut:
    workflow = await _get(session, workflow_id)
    row = await wf.get_version(session, workflow_id, number)
    if row is None:
        raise HTTPException(404, f"This workflow has no version {number}")
    return await _version_out(session, workflow, row)


@router.post("/{workflow_id}/versions", response_model=WorkflowVersionOut, status_code=201)
async def publish_version(
    workflow_id: str, body: WorkflowPublish, session: SessionDep, services: ServicesDep
) -> WorkflowVersionOut:
    """Publish the draft, as it is at `revision`, as the next version."""
    workflow = await _get(session, workflow_id)
    if body.revision != workflow.revision:
        raise HTTPException(409, CHANGED_ELSEWHERE)
    try:
        row = await wf.publish(session, workflow, body.note)
        await session.commit()
    except wf.WorkflowInvalid as exc:
        raise HTTPException(422, str(exc)) from exc
    except wf.NothingToPublish as exc:
        raise HTTPException(409, str(exc)) from exc
    except IntegrityError as exc:
        # Another publish took the same number first.
        await session.rollback()
        raise HTTPException(409, CHANGED_ELSEWHERE) from exc
    services.bus.notify("workflow", workflow.id)
    return await _version_out(session, workflow, row)


@router.post("/{workflow_id}/versions/{number}/restore", response_model=WorkflowOut)
async def restore_version(
    workflow_id: str,
    number: int,
    body: WorkflowRestore,
    session: SessionDep,
    services: ServicesDep,
) -> WorkflowOut:
    """Replace the draft's name, description and steps with a version's. The version stays."""
    workflow = await _get(session, workflow_id)
    if body.revision != workflow.revision:
        raise HTTPException(409, CHANGED_ELSEWHERE)
    row = await wf.get_version(session, workflow_id, number)
    if row is None:
        raise HTTPException(404, f"This workflow has no version {number}")
    workflow.name, workflow.description = row.name, row.description
    workflow.spec = wf.spec_of(row).model_dump(mode="json")
    workflow.revision += 1
    await session.commit()
    services.bus.notify("workflow", workflow.id)
    return await _out(session, workflow)


# -- runs -------------------------------------------------------------------------------------
@router.post("/{workflow_id}/runs", response_model=RunOut, status_code=201)
async def run_workflow(
    workflow_id: str,
    body: WorkflowRunCreate,
    services: ServicesDep,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key", max_length=200),
) -> RunOut:
    """Run a published version or the draft, on an existing task or a new task made for it."""
    async with services.db.session() as session:
        if idempotency_key:
            existing = await session.scalar(
                select(AgentRun).where(AgentRun.idempotency_key == idempotency_key)
            )
            if existing is not None:
                return await run_detail(session, services, existing.id)
        row = await _get(session, workflow_id)
        if body.version is None:
            if body.revision != row.revision:
                raise HTTPException(
                    409,
                    "This workflow changed after you chose to run it. Check the latest version and run it again.",
                )
            issues = await wf.check(session, row)
            if errors(issues):
                raise HTTPException(422, wf.describe_errors(issues, wf.spec_of(row)))
        else:
            try:
                await wf.source_for_run(session, row, body.version)
            except LookupError as exc:
                raise HTTPException(404, str(exc)) from exc
            except wf.WorkflowInvalid as exc:
                raise HTTPException(422, str(exc)) from exc
        new_task_id = None
        if body.new_task is not None:
            new_task_id = await _create_task_for_run(session, services, body.new_task)
            task_id = new_task_id
        else:
            assert body.task_id is not None
            if await session.get(Task, body.task_id) is None:
                raise HTTPException(404, "Task not found")
            task_id = body.task_id
    request = RunRequest(
        workflow_id=workflow_id,
        workflow_version=body.version,
        workflow_revision=None if body.version else body.revision,
    )
    try:
        run = await services.runs.start(task_id, request, idempotency_key)
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


# -- templates --------------------------------------------------------------------------------
@templates_router.get("", response_model=list[TemplateOut])
async def list_templates(session: SessionDep) -> list[TemplateOut]:
    """The built-in templates, with what still needs configuring before one can run here."""
    agents = await wf.agent_refs(session)
    workspaces = await session.scalar(select(func.count(Workspace.id))) or 0
    settings = await load_settings(session)
    return [
        TemplateOut(
            key=t.key,
            name=t.name,
            description=t.description,
            outline=t.outline,
            spec=WorkflowSpec(steps=list(t.steps)),
            setup=setup(
                t,
                agents,
                workspaces=workspaces,
                test_commands=len(settings.execution.test_commands),
            ),
        )
        for t in TEMPLATES
    ]
