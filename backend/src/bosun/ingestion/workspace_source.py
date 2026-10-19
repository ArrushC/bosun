"""Workspace connector: wraps the scanner as a `SourceConnector`."""

from __future__ import annotations

import asyncio
from pathlib import Path

from bosun.domain.enums import SourceRecordState
from bosun.ingestion.base import CollectResult, ExternalItem, SourceConnector
from bosun.ingestion.workspace_scanner import ScanOptions, scan_workspace


class WorkspaceConnector(SourceConnector):
    kind = "workspace"

    def __init__(self, workspace_id: str, root: Path, options: ScanOptions) -> None:
        self._workspace_id = workspace_id
        self._root = root
        self._options = options

    @property
    def scope(self) -> str:
        return self._workspace_id

    async def collect(self, known_open: dict[str, int | None]) -> CollectResult:
        result = await asyncio.to_thread(scan_workspace, self._root, self._options)
        items = [
            ExternalItem(
                kind=item.kind,
                external_id=item.external_id,
                title=item.title,
                body=item.body,
                path=item.path,
                line=item.line,
                context=item.context,
                tag=item.tag,
                meta={**item.meta, "branch": result.git_branch} if result.git_branch else item.meta,
                content_hash=item.content_hash,
            )
            for item in result.items
        ]
        return CollectResult(
            items=items,
            complete=not result.stats.truncated,
            missing_state=SourceRecordState.RESOLVED,
            summary=result.summary(),
            errors=result.errors,
        )
