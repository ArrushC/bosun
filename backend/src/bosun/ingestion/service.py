"""Ingestion orchestration: workspace scans, GitHub syncs and periodic synchronisation.

Discovery never creates tasks or starts agents by itself: it only maintains
`SourceRecord`s. Humans promote records to tasks from the Inbox.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import select

from bosun.config import APP_ROOT
from bosun.db.models import SourceConnection, SourceRecord, TaskSourceLink, Workspace
from bosun.domain.enums import ConnectionStatus, SourceRecordState, WorkspaceStatus
from bosun.ids import utcnow
from bosun.ingestion.github.client import (
    GitHubAuthError,
    GitHubClient,
    GitHubError,
    GitHubRateLimited,
    GitHubUnavailable,
    resolve_token,
)
from bosun.ingestion.github.connector import GitHubConnectionConfig, GitHubConnector
from bosun.ingestion.records import apply_collect_result
from bosun.ingestion.workspace_scanner import ScanOptions
from bosun.ingestion.workspace_source import WorkspaceConnector
from bosun.settings_store import ScanSettings

log = logging.getLogger(__name__)

SYNC_TICK_S = 60.0


class IngestionError(Exception):
    pass


class IngestionService:
    def __init__(self, services: Any) -> None:
        self.services = services
        self._locks: dict[str, asyncio.Lock] = {}
        self._loop_task: asyncio.Task[None] | None = None
        # Tests and alternative deployments can inject an httpx transport.
        self.github_transport: Any = None

    def _lock(self, key: str) -> asyncio.Lock:
        return self._locks.setdefault(key, asyncio.Lock())

    # -- workspaces ---------------------------------------------------------------
    def scan_options(
        self, scan: ScanSettings, overrides: dict[str, Any] | None = None, root: Path | None = None
    ) -> ScanOptions:
        merged = {**scan.model_dump(), **(overrides or {})}
        # Bosun's own files are excluded when they live inside the scanned
        # workspace — but not when the workspace itself lives inside them (demo data).
        excluded = [
            p.resolve()
            for p in (APP_ROOT, self.services.cfg.data_dir)
            if root is None or not root.resolve().is_relative_to(p.resolve())
        ]
        return ScanOptions(
            tags=list(merged["tags"]),
            exclude_globs=list(merged["exclude_globs"]),
            max_file_kb=int(merged["max_file_kb"]),
            max_files=int(merged["max_files"]),
            include_markdown_tasks=bool(merged["include_markdown_tasks"]),
            include_todo_files=bool(merged["include_todo_files"]),
            excluded_paths=excluded,
        )

    async def scan_workspace(self, workspace_id: str) -> dict[str, Any]:
        async with self._lock(f"ws:{workspace_id}"), self.services.db.session() as session:
            workspace = await session.get(Workspace, workspace_id)
            if workspace is None:
                raise IngestionError("Workspace not found")
            root = Path(workspace.path)
            if not root.is_dir():
                workspace.status = WorkspaceStatus.MISSING
                await session.commit()
                self.services.bus.notify("workspace", workspace_id)
                raise IngestionError(f"Workspace directory {root} no longer exists")
            settings = await self.services.load_settings()
            options = self.scan_options(settings.scan, workspace.scan_settings, root)
            connector = WorkspaceConnector(workspace.id, root, options)
            result = await connector.collect({})
            summary = await apply_collect_result(
                session,
                scope=workspace.id,
                result=result,
                workspace_id=workspace.id,
                is_demo=workspace.is_demo,
            )
            workspace.status = WorkspaceStatus.OK
            workspace.last_scanned_at = utcnow()
            workspace.last_scan_summary = {**result.summary, **summary.as_dict()}
            await session.commit()
            full = workspace.last_scan_summary
        self.services.bus.notify("workspace", workspace_id, action="scanned")
        self.services.bus.notify("sources", None)
        for task_id in summary.changed_task_ids:
            self.services.bus.notify("task", task_id)
        return full

    async def refresh_workspace_status(self) -> None:
        async with self.services.db.session() as session:
            for workspace in (await session.scalars(select(Workspace))).all():
                status = (
                    WorkspaceStatus.OK if Path(workspace.path).is_dir() else WorkspaceStatus.MISSING
                )
                if workspace.status != status:
                    workspace.status = status
            await session.commit()

    # -- GitHub ------------------------------------------------------------------
    def github_client(self) -> GitHubClient:
        token, _source = resolve_token(self.services.cfg.github_token)
        return GitHubClient(
            token, self.services.cfg.github_api_url, transport=self.github_transport
        )

    async def github_status(self) -> dict[str, Any]:
        token, source = resolve_token(self.services.cfg.github_token)
        if not token:
            hint = {
                "none": "Install the GitHub CLI and run `gh auth login`, or set BOSUN_GITHUB_TOKEN.",
                "gh-not-logged-in": "Run `gh auth login` to connect your GitHub account.",
                "gh-error": "The GitHub CLI did not respond. Check `gh auth status`.",
            }.get(source, "No GitHub credentials found.")
            return {"connected": False, "source": source, "message": hint}
        async with GitHubClient(
            token, self.services.cfg.github_api_url, transport=self.github_transport
        ) as client:
            try:
                viewer = await client.viewer()
            except GitHubAuthError:
                return {
                    "connected": False,
                    "source": source,
                    "message": "GitHub rejected the token. Re-run `gh auth login`.",
                }
            except GitHubRateLimited as exc:
                return {
                    "connected": True,
                    "source": source,
                    "message": str(exc),
                    "rate_limited_until": exc.reset_at.isoformat(),
                }
            except GitHubError as exc:
                return {"connected": False, "source": source, "message": str(exc)}
            rl = client.rate_limit
            return {
                "connected": True,
                "source": source,
                "login": viewer.get("login"),
                "name": viewer.get("name"),
                "avatar_url": viewer.get("avatar_url"),
                "scopes": client.scopes,
                "rate_limit": {
                    "limit": rl.limit,
                    "remaining": rl.remaining,
                    "reset_at": rl.reset_at.isoformat() if rl.reset_at else None,
                },
                "read_only": True,
            }

    async def sync_connection(self, connection_id: str) -> dict[str, Any]:
        async with self._lock(f"conn:{connection_id}"):
            async with self.services.db.session() as session:
                connection = await session.get(SourceConnection, connection_id)
                if connection is None:
                    raise IngestionError("Connection not found")
                config = GitHubConnectionConfig.model_validate(connection.config)
                connection.status = ConnectionStatus.SYNCING
                await session.commit()
                since = connection.last_synced_at
            self.services.bus.notify("connection", connection_id)

            status, error, summary_out = ConnectionStatus.OK, None, {}
            rate_limit_reset = None
            try:
                async with self.github_client() as client:
                    async with self.services.db.session() as session:
                        known = {
                            r.external_id: r.number
                            for r in (
                                await session.scalars(
                                    select(SourceRecord).where(
                                        SourceRecord.scope == connection_id,
                                        SourceRecord.state == SourceRecordState.OPEN,
                                    )
                                )
                            ).all()
                        }
                    connector = GitHubConnector(connection_id, config, client, since=since)
                    result = await connector.collect(known)
                    async with self.services.db.session() as session:
                        upsert = await apply_collect_result(
                            session, scope=connection_id, result=result, connection_id=connection_id
                        )
                        await session.commit()
                    summary_out = {
                        **result.summary,
                        **upsert.as_dict(),
                        "errors": result.errors[:20],
                    }
                    for task_id in upsert.changed_task_ids:
                        self.services.bus.notify("task", task_id)
                    if "rate_limited_until" in result.summary:
                        status = ConnectionStatus.RATE_LIMITED
                        rate_limit_reset = result.summary["rate_limited_until"]
                        error = f"GitHub rate limit reached; sync resumes after {rate_limit_reset}"
                    elif result.errors:
                        status, error = ConnectionStatus.ERROR, "; ".join(result.errors[:3])
            except GitHubAuthError as exc:
                status, error = (
                    ConnectionStatus.ERROR,
                    f"{exc}. Re-run `gh auth login` or update BOSUN_GITHUB_TOKEN.",
                )
            except GitHubUnavailable as exc:
                status, error = ConnectionStatus.ERROR, f"{exc}. Check your network connection."
            except GitHubRateLimited as exc:
                status, error, rate_limit_reset = (
                    ConnectionStatus.RATE_LIMITED,
                    str(exc),
                    exc.reset_at.isoformat(),
                )
            except GitHubError as exc:
                status, error = ConnectionStatus.ERROR, str(exc)

            async with self.services.db.session() as session:
                connection = await session.get(SourceConnection, connection_id)
                if connection is not None:
                    connection.status = status
                    connection.last_error = error
                    connection.last_sync_summary = summary_out or connection.last_sync_summary
                    if status != ConnectionStatus.ERROR or summary_out:
                        connection.last_synced_at = utcnow()
                    if rate_limit_reset:
                        connection.rate_limit_reset_at = datetime.fromisoformat(
                            str(rate_limit_reset)
                        )
                    await session.commit()
        self.services.bus.notify("connection", connection_id, action="synced")
        self.services.bus.notify("sources", None)
        return {"status": status, "error": error, "summary": summary_out}

    async def prune_removed_repos(self, connection_id: str, repos: list[str]) -> int:
        """Drop untriaged records from repositories removed from a connection."""
        removed = 0
        async with self.services.db.session() as session:
            records = (
                await session.scalars(
                    select(SourceRecord).where(SourceRecord.scope == connection_id)
                )
            ).all()
            for record in records:
                if record.repo in repos:
                    continue
                linked = await session.scalar(
                    select(TaskSourceLink.task_id).where(
                        TaskSourceLink.source_record_id == record.id
                    )
                )
                if linked:
                    continue
                await session.delete(record)
                removed += 1
            await session.commit()
        return removed

    # -- periodic sync ------------------------------------------------------------
    def start_background_sync(self) -> None:
        if self._loop_task is None:
            self._loop_task = asyncio.create_task(self._sync_loop(), name="github-sync-loop")

    async def stop_background_sync(self) -> None:
        if self._loop_task is not None:
            self._loop_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._loop_task
            self._loop_task = None

    async def _sync_loop(self) -> None:
        while True:
            await asyncio.sleep(SYNC_TICK_S)
            try:
                await self._sync_due()
            except Exception:  # noqa: BLE001
                log.exception("Periodic sync failed")

    async def _sync_due(self) -> None:
        now = utcnow()
        async with self.services.db.session() as session:
            connections = (await session.scalars(select(SourceConnection))).all()
        for connection in connections:
            config = GitHubConnectionConfig.model_validate(connection.config)
            if not config.sync_interval_minutes or connection.status == ConnectionStatus.SYNCING:
                continue
            if connection.rate_limit_reset_at and connection.rate_limit_reset_at > now:
                continue
            last = connection.last_synced_at
            if last is None or now - last >= timedelta(minutes=config.sync_interval_minutes):
                await self.sync_connection(connection.id)
