"""Discovered work (the Inbox): browse, dismiss, and promote records to tasks."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import Select, func, or_, select

from bosun.api.deps import ServicesDep, SessionDep
from bosun.api.schemas import (
    DismissRequest,
    ImportRequest,
    SourceRecordOut,
    SourceRecordPage,
    TaskOut,
)
from bosun.api.serialize import tasks_out
from bosun.db.models import SourceConnection, SourceRecord, Task, TaskSourceLink
from bosun.tasks.service import import_records, load_task

router = APIRouter(prefix="/sources", tags=["sources"])


@router.get("/records", response_model=SourceRecordPage)
async def list_records(
    session: SessionDep,
    state: str = Query(default="open"),
    kind: str | None = None,
    workspace_id: str | None = None,
    connection_id: str | None = None,
    q: str | None = None,
    triage: str = Query(default="untriaged", pattern="^(untriaged|imported|dismissed|all)$"),
    limit: int = Query(default=200, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
) -> SourceRecordPage:
    """`workspace_id` also matches records from GitHub connections linked to it."""
    # A record's project: the workspace it was found in, or the one its GitHub
    # connection is linked to. A connection linked to none is its own project.
    project = func.coalesce(
        SourceRecord.workspace_id, SourceConnection.workspace_id, SourceRecord.connection_id
    )
    linked = select(TaskSourceLink.source_record_id)
    filters = []
    if state != "all":
        filters.append(SourceRecord.state == state)
    if kind:
        filters.append(SourceRecord.kind.in_(kind.split(",")))
    if q:
        like = f"%{q}%"
        filters.append(
            or_(
                SourceRecord.title.ilike(like),
                SourceRecord.path.ilike(like),
                SourceRecord.body.ilike(like),
                SourceRecord.repo.ilike(like),
            )
        )
    scope = []
    if workspace_id:
        scope.append(project == workspace_id)
    if connection_id:
        scope.append(SourceRecord.connection_id == connection_id)
    triages = {
        "untriaged": (SourceRecord.dismissed.is_(False)) & (SourceRecord.id.not_in(linked)),
        "imported": SourceRecord.id.in_(linked),
        "dismissed": SourceRecord.dismissed.is_(True),
    }
    chosen = [triages[triage]] if triage in triages else []

    def records(*where: Any) -> Select[SourceRecord]:
        return (
            select(SourceRecord)
            .outerjoin(SourceConnection, SourceConnection.id == SourceRecord.connection_id)
            .where(*filters, *where)
        )

    stmt = records(*scope, *chosen)
    total = await session.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = (
        await session.scalars(
            stmt.order_by(
                SourceRecord.kind, SourceRecord.path, SourceRecord.line, SourceRecord.number.desc()
            )
            .offset(offset)
            .limit(limit)
        )
    ).all()
    links: dict[str, list[str]] = {}
    if rows:
        for record_id, task_id in (
            await session.execute(
                select(TaskSourceLink.source_record_id, TaskSourceLink.task_id).where(
                    TaskSourceLink.source_record_id.in_([r.id for r in rows])
                )
            )
        ).all():
            links.setdefault(record_id, []).append(task_id)

    counts = {}
    for label, clause in triages.items():
        counts[label] = (
            await session.scalar(
                select(func.count()).select_from(records(*scope, clause).subquery())
            )
            or 0
        )
    by_project = await session.execute(
        select(project, func.count())
        .select_from(SourceRecord)
        .outerjoin(SourceConnection, SourceConnection.id == SourceRecord.connection_id)
        .where(*filters, *chosen)
        .group_by(project)
    )
    projects = {key: n for key, n in by_project.tuples() if key}

    items = []
    for row in rows:
        item = SourceRecordOut.model_validate(row)
        item.task_ids = links.get(row.id, [])
        items.append(item)
    return SourceRecordPage(items=items, total=total, counts=counts, projects=projects)


@router.post("/records/import", response_model=list[TaskOut])
async def import_to_tasks(
    body: ImportRequest, session: SessionDep, services: ServicesDep
) -> list[TaskOut]:
    records = (
        await session.scalars(select(SourceRecord).where(SourceRecord.id.in_(body.record_ids)))
    ).all()
    if len(records) != len(set(body.record_ids)):
        raise HTTPException(404, "Some records were not found")
    workspace_for: dict[str, str | None] = {}
    for record in records:
        if record.connection_id and not record.workspace_id:
            connection = await session.get(SourceConnection, record.connection_id)
            workspace_for[record.id] = body.workspace_id or (
                connection.workspace_id if connection else None
            )
    tasks = []
    for record in records:
        created = await import_records(
            session,
            [record],
            status=body.status,
            workspace_id=workspace_for.get(record.id, body.workspace_id),
            execution_mode=body.execution_mode,
        )
        tasks.extend(created)
    await session.commit()
    loaded = [t for t in [await load_task(session, t.id) for t in tasks] if t is not None]
    services.bus.notify("task", None, action="created")
    services.bus.notify("sources", None)
    return await tasks_out(session, loaded)


@router.post("/records/dismiss")
async def dismiss(
    body: DismissRequest, session: SessionDep, services: ServicesDep
) -> dict[str, int]:
    records = (
        await session.scalars(select(SourceRecord).where(SourceRecord.id.in_(body.record_ids)))
    ).all()
    for record in records:
        record.dismissed = body.dismissed
    await session.commit()
    services.bus.notify("sources", None)
    return {"updated": len(records)}


@router.get("/records/{record_id}", response_model=SourceRecordOut)
async def get_record(record_id: str, session: SessionDep) -> SourceRecordOut:
    record = await session.get(SourceRecord, record_id)
    if record is None:
        raise HTTPException(404, "Record not found")
    item = SourceRecordOut.model_validate(record)
    item.task_ids = list(
        (
            await session.scalars(
                select(Task.id)
                .join(TaskSourceLink)
                .where(TaskSourceLink.source_record_id == record_id)
            )
        ).all()
    )
    return item
