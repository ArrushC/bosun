"""Source connector contract.

A connector turns an external system (local workspace, GitHub, and later Jira,
Linear or MCP-backed tools) into a stream of `ExternalItem`s. The core
ingestion code upserts those items into `SourceRecord`s keyed by
`(scope, external_id)`; the task engine never talks to connectors directly.

To add a new integration, implement `SourceConnector.collect()` and register
the connector kind in `ingestion/registry.py`.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import ClassVar

from bosun.domain.enums import SourceRecordState


@dataclass
class ExternalItem:
    kind: str
    external_id: str
    title: str
    body: str | None = None
    url: str | None = None
    path: str | None = None
    line: int | None = None
    context: str | None = None
    tag: str | None = None
    repo: str | None = None
    number: int | None = None
    author: str | None = None
    labels: list[str] = field(default_factory=list)
    meta: dict[str, object] = field(default_factory=dict)
    state: str = SourceRecordState.OPEN
    external_updated_at: datetime | None = None
    content_hash: str | None = None


@dataclass
class CollectResult:
    items: list[ExternalItem]
    # When True, previously open records in this scope that were not returned
    # are no longer present at the source and become `missing_state`.
    complete: bool = True
    missing_state: str = SourceRecordState.RESOLVED
    # Explicit state changes for records the connector verified individually
    # (e.g. GitHub issues that were closed or deleted since the last sync).
    state_updates: dict[str, str] = field(default_factory=dict)
    summary: dict[str, object] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)


class SourceConnector(ABC):
    kind: ClassVar[str]

    @property
    @abstractmethod
    def scope(self) -> str:
        """Deduplication scope (workspace id, connection id, ...)."""

    @abstractmethod
    async def collect(self, known_open: dict[str, int | None]) -> CollectResult:
        """Return the current items at the source.

        `known_open` maps external ids of records currently open in this scope
        to their source number (if any), letting connectors verify records
        that disappeared from a listing.
        """
