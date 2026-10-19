from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException
from sqlalchemy import func, select

from bosun.api.deps import ServicesDep, SessionDep
from bosun.api.schemas import WorkspaceCreate, WorkspaceOut, WorkspaceUpdate
from bosun.db.models import SourceRecord, Task, Workspace
from bosun.domain.enums import SourceRecordState
from bosun.ingestion.file_walker import is_git_repo
from bosun.ingestion.service import IngestionError
from bosun.ingestion.workspace_scanner import git_branch
from bosun.settings_store import ScanSettings

router = APIRouter(prefix="/workspaces", tags=["workspaces"])


async def _out(session: SessionDep, workspaces: list[Workspace]) -> list[WorkspaceOut]:
    ids = [w.id for w in workspaces]
    open_items = dict(
        (
            await session.execute(
                select(SourceRecord.workspace_id, func.count(SourceRecord.id))
                .where(
                    SourceRecord.workspace_id.in_(ids),
                    SourceRecord.state == SourceRecordState.OPEN,
                    SourceRecord.dismissed.is_(False),
                )
                .group_by(SourceRecord.workspace_id)
            )
        ).all()
    )
    tasks = dict(
        (
            await session.execute(
                select(Task.workspace_id, func.count(Task.id))
                .where(Task.workspace_id.in_(ids), Task.archived.is_(False))
                .group_by(Task.workspace_id)
            )
        ).all()
    )
    out = []
    for w in workspaces:
        item = WorkspaceOut.model_validate(w)
        item.open_items = open_items.get(w.id, 0)
        item.task_count = tasks.get(w.id, 0)
        out.append(item)
    return out


@router.get("", response_model=list[WorkspaceOut])
async def list_workspaces(session: SessionDep) -> list[WorkspaceOut]:
    rows = (await session.scalars(select(Workspace).order_by(Workspace.created_at))).all()
    return await _out(session, list(rows))


@router.post("", response_model=WorkspaceOut, status_code=201)
async def create_workspace(
    body: WorkspaceCreate, session: SessionDep, services: ServicesDep
) -> WorkspaceOut:
    path = Path(body.path).expanduser()
    if not path.is_absolute():
        raise HTTPException(400, "Workspace path must be absolute")
    path = path.resolve()
    if not path.is_dir():
        raise HTTPException(400, f"{path} is not a directory")
    if await session.scalar(select(Workspace).where(Workspace.path == str(path))):
        raise HTTPException(409, "This directory is already a workspace")
    is_git = is_git_repo(path)
    workspace = Workspace(
        name=body.name or path.name,
        path=str(path),
        is_git=is_git,
        default_branch=git_branch(path) if is_git else None,
        trusted=body.trusted,
    )
    session.add(workspace)
    await session.commit()
    services.bus.notify("workspace", workspace.id, action="created")
    if body.scan:
        try:
            await services.ingestion.scan_workspace(workspace.id)
        except IngestionError as exc:
            raise HTTPException(400, str(exc)) from exc
        await session.refresh(workspace)
    return (await _out(session, [workspace]))[0]


@router.patch("/{workspace_id}", response_model=WorkspaceOut)
async def update_workspace(
    workspace_id: str, body: WorkspaceUpdate, session: SessionDep, services: ServicesDep
) -> WorkspaceOut:
    workspace = await session.get(Workspace, workspace_id)
    if workspace is None:
        raise HTTPException(404, "Workspace not found")
    if body.name is not None:
        workspace.name = body.name
    if body.trusted is not None:
        workspace.trusted = body.trusted
    if body.scan_settings is not None:
        allowed = set(ScanSettings.model_fields)
        workspace.scan_settings = {k: v for k, v in body.scan_settings.items() if k in allowed}
    await session.commit()
    services.bus.notify("workspace", workspace_id)
    return (await _out(session, [workspace]))[0]


@router.delete("/{workspace_id}", status_code=204)
async def delete_workspace(workspace_id: str, session: SessionDep, services: ServicesDep) -> None:
    workspace = await session.get(Workspace, workspace_id)
    if workspace is None:
        raise HTTPException(404, "Workspace not found")
    await session.delete(workspace)
    await session.commit()
    services.bus.notify("workspace", workspace_id, action="deleted")


@router.post("/{workspace_id}/scan")
async def scan_workspace(workspace_id: str, services: ServicesDep) -> dict[str, object]:
    try:
        return await services.ingestion.scan_workspace(workspace_id)
    except IngestionError as exc:
        raise HTTPException(400, str(exc)) from exc
