"""GitHub connection management (read-only ingestion)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import func, select

from bosun.api.deps import ServicesDep, SessionDep
from bosun.api.schemas import (
    ConnectionCreate,
    ConnectionOut,
    ConnectionUpdate,
    GitHubRepoOut,
)
from bosun.db.models import SourceConnection, SourceRecord
from bosun.domain.enums import SourceRecordState
from bosun.ingestion.github.client import GitHubError, GitHubRateLimited
from bosun.ingestion.github.connector import GitHubConnectionConfig, GitHubFilters

router = APIRouter(tags=["github"])


@router.get("/github/status")
async def github_status(services: ServicesDep) -> dict[str, object]:
    return await services.ingestion.github_status()


@router.get("/github/repos", response_model=list[GitHubRepoOut])
async def github_repos(
    services: ServicesDep, q: str | None = Query(default=None)
) -> list[GitHubRepoOut]:
    async with services.ingestion.github_client() as client:
        try:
            repos = await client.list_repos(q, limit=300)
        except GitHubRateLimited as exc:
            raise HTTPException(429, str(exc)) from exc
        except GitHubError as exc:
            raise HTTPException(502, str(exc)) from exc
    return [
        GitHubRepoOut(
            full_name=r["full_name"],
            description=r.get("description"),
            private=bool(r.get("private")),
            open_issues_count=int(r.get("open_issues_count") or 0),
            html_url=r["html_url"],
            pushed_at=r.get("pushed_at"),
            default_branch=r.get("default_branch"),
        )
        for r in repos
    ]


@router.get("/github/repos/{owner}/{name}/labels")
async def github_labels(owner: str, name: str, services: ServicesDep) -> list[dict[str, Any]]:
    async with services.ingestion.github_client() as client:
        try:
            labels = await client.list_labels(f"{owner}/{name}")
            milestones = await client.list_milestones(f"{owner}/{name}")
        except GitHubError as exc:
            raise HTTPException(502, str(exc)) from exc
    return [
        {"type": "label", "name": label["name"], "color": label.get("color")} for label in labels
    ] + [{"type": "milestone", "name": m["title"], "state": m.get("state")} for m in milestones]


async def _connections_out(
    session: SessionDep, rows: list[SourceConnection]
) -> list[ConnectionOut]:
    counts = dict(
        (
            await session.execute(
                select(SourceRecord.connection_id, func.count(SourceRecord.id))
                .where(
                    SourceRecord.state == SourceRecordState.OPEN,
                    SourceRecord.connection_id.in_([r.id for r in rows]),
                )
                .group_by(SourceRecord.connection_id)
            )
        ).all()
    )
    out = []
    for row in rows:
        item = ConnectionOut.model_validate(row)
        item.open_items = counts.get(row.id, 0)
        out.append(item)
    return out


@router.get("/connections", response_model=list[ConnectionOut])
async def list_connections(session: SessionDep) -> list[ConnectionOut]:
    rows = list(
        (
            await session.scalars(select(SourceConnection).order_by(SourceConnection.created_at))
        ).all()
    )
    return await _connections_out(session, rows)


@router.post("/connections", response_model=ConnectionOut, status_code=201)
async def create_connection(
    body: ConnectionCreate, session: SessionDep, services: ServicesDep
) -> ConnectionOut:
    repos = sorted({r.strip() for r in body.repos if r.strip().count("/") == 1})
    if not repos:
        raise HTTPException(400, "Provide repositories as owner/name")
    config = GitHubConnectionConfig(
        repos=repos,
        filters=GitHubFilters(**body.filters.model_dump()),
        sync_interval_minutes=body.sync_interval_minutes,
    )
    connection = SourceConnection(
        kind="github",
        name=body.name or (repos[0] if len(repos) == 1 else f"{repos[0]} +{len(repos) - 1}"),
        config=config.model_dump(),
        workspace_id=body.workspace_id,
    )
    session.add(connection)
    await session.commit()
    services.bus.notify("connection", connection.id, action="created")
    if body.sync_now:
        await services.ingestion.sync_connection(connection.id)
        await session.refresh(connection)
    return (await _connections_out(session, [connection]))[0]


@router.patch("/connections/{connection_id}", response_model=ConnectionOut)
async def update_connection(
    connection_id: str, body: ConnectionUpdate, session: SessionDep, services: ServicesDep
) -> ConnectionOut:
    connection = await session.get(SourceConnection, connection_id)
    if connection is None:
        raise HTTPException(404, "Connection not found")
    config = GitHubConnectionConfig.model_validate(connection.config)
    if body.repos is not None:
        config.repos = sorted({r.strip() for r in body.repos if r.strip().count("/") == 1})
    if body.filters is not None:
        config.filters = config.filters.model_validate(body.filters.model_dump())
    if body.sync_interval_minutes is not None:
        config.sync_interval_minutes = body.sync_interval_minutes
    connection.config = config.model_dump()
    if body.name is not None:
        connection.name = body.name
    if "workspace_id" in body.model_fields_set:
        connection.workspace_id = body.workspace_id
    await session.commit()
    if body.repos is not None:
        await services.ingestion.prune_removed_repos(connection_id, config.repos)
    services.bus.notify("connection", connection_id)
    return (await _connections_out(session, [connection]))[0]


@router.delete("/connections/{connection_id}", status_code=204)
async def delete_connection(connection_id: str, session: SessionDep, services: ServicesDep) -> None:
    connection = await session.get(SourceConnection, connection_id)
    if connection is None:
        raise HTTPException(404, "Connection not found")
    await session.delete(connection)
    await session.commit()
    services.bus.notify("connection", connection_id, action="deleted")
    services.bus.notify("sources", None)


@router.post("/connections/{connection_id}/sync")
async def sync_connection(connection_id: str, services: ServicesDep) -> dict[str, object]:
    return await services.ingestion.sync_connection(connection_id)
