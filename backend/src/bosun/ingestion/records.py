"""Upsert connector output into `SourceRecord`s and propagate source state to tasks."""

from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bosun.db.models import SourceRecord, Task, TaskActivity, TaskSourceLink
from bosun.domain.enums import SourceRecordState
from bosun.ids import utcnow
from bosun.ingestion.base import CollectResult, ExternalItem

_CLOSED_STATES = {
    SourceRecordState.CLOSED,
    SourceRecordState.MERGED,
    SourceRecordState.RESOLVED,
    SourceRecordState.DELETED,
}

_STATE_MESSAGES = {
    SourceRecordState.CLOSED: "Source was closed",
    SourceRecordState.MERGED: "Source pull request was merged",
    SourceRecordState.RESOLVED: "Source annotation no longer present",
    SourceRecordState.DELETED: "Source was deleted",
    SourceRecordState.OPEN: "Source was reopened",
}


@dataclass
class UpsertSummary:
    created: int = 0
    updated: int = 0
    unchanged: int = 0
    state_changes: int = 0
    changed_task_ids: set[str] = field(default_factory=set)

    def as_dict(self) -> dict[str, int]:
        return {
            "created": self.created,
            "updated": self.updated,
            "unchanged": self.unchanged,
            "state_changes": self.state_changes,
        }


def _apply_fields(record: SourceRecord, item: ExternalItem) -> bool:
    changed = False
    for attr in (
        "title", "body", "url", "path", "line", "context", "tag", "repo", "number",
        "author", "labels", "meta", "external_updated_at", "content_hash",
    ):  # fmt: skip
        value = getattr(item, attr)
        if getattr(record, attr) != value:
            setattr(record, attr, value)
            if attr not in ("line", "context", "external_updated_at"):
                changed = True
    return changed


async def _record_task_activity(
    session: AsyncSession, record: SourceRecord, new_state: str, summary: UpsertSummary
) -> None:
    task_ids = (
        await session.scalars(
            select(TaskSourceLink.task_id).where(TaskSourceLink.source_record_id == record.id)
        )
    ).all()
    for task_id in task_ids:
        task = await session.get(Task, task_id)
        if task is None:
            continue
        task.source_state = new_state if new_state in _CLOSED_STATES else None
        message = (
            f"{_STATE_MESSAGES.get(SourceRecordState(new_state), 'Source changed')}: {record.title}"
        )
        task.last_activity_at = utcnow()
        task.last_activity = message
        session.add(
            TaskActivity(
                task_id=task_id,
                kind="source_state",
                actor="system",
                message=message,
                data={"record_id": record.id, "state": new_state, "url": record.url},
            )
        )
        summary.changed_task_ids.add(task_id)


async def apply_collect_result(
    session: AsyncSession,
    *,
    scope: str,
    result: CollectResult,
    workspace_id: str | None = None,
    connection_id: str | None = None,
    is_demo: bool = False,
) -> UpsertSummary:
    summary = UpsertSummary()
    now = utcnow()
    existing = {
        r.external_id: r
        for r in (
            await session.scalars(select(SourceRecord).where(SourceRecord.scope == scope))
        ).all()
    }
    seen: set[str] = set()

    for item in result.items:
        seen.add(item.external_id)
        record = existing.get(item.external_id)
        if record is None:
            record = SourceRecord(
                kind=item.kind,
                scope=scope,
                external_id=item.external_id,
                workspace_id=workspace_id,
                connection_id=connection_id,
                title=item.title,
                state=item.state,
                first_seen_at=now,
                last_seen_at=now,
                is_demo=is_demo,
            )
            _apply_fields(record, item)
            session.add(record)
            existing[item.external_id] = record
            summary.created += 1
            continue
        changed = _apply_fields(record, item)
        record.last_seen_at = now
        if record.kind != item.kind:
            record.kind = item.kind
        if record.state != item.state:
            record.state = item.state
            summary.state_changes += 1
            await _record_task_activity(session, record, item.state, summary)
            changed = True
        if changed:
            summary.updated += 1
        else:
            summary.unchanged += 1

    for external_id, state in result.state_updates.items():
        record = existing.get(external_id)
        if record is not None and record.state != state:
            record.state = state
            summary.state_changes += 1
            await _record_task_activity(session, record, state, summary)

    if result.complete:
        for external_id, record in existing.items():
            if external_id in seen or external_id in result.state_updates:
                continue
            if record.state == SourceRecordState.OPEN:
                record.state = result.missing_state
                summary.state_changes += 1
                await _record_task_activity(session, record, result.missing_state, summary)

    await session.flush()
    return summary
