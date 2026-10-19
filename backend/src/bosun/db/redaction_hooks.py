"""Redact secrets from free-text columns that agents and tools write.

Event payloads are redacted by the event bus; this covers the columns that hold
agent output outside events (session results, run summaries, subtask reports,
approval titles), so a secret printed by a tool never reaches the database.
Bulk `update()` statements bypass ORM flush events and must redact themselves.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import event
from sqlalchemy.orm import Session

from bosun.db.models import (
    AgentRun,
    AgentSession,
    ApprovalRequest,
    SourceConnection,
    Subtask,
    TaskActivity,
)
from bosun.security.redaction import redact, redact_text

TEXT_COLUMNS: dict[type, tuple[str, ...]] = {
    AgentSession: ("prompt", "result", "error"),
    AgentRun: ("summary", "error", "status_reason"),
    Subtask: ("report", "error"),
    TaskActivity: ("message",),
    ApprovalRequest: ("title", "description"),
    SourceConnection: ("last_error",),
}
JSON_COLUMNS: dict[type, tuple[str, ...]] = {
    AgentSession: ("structured_output",),
    ApprovalRequest: ("resolution",),
}


def _redact_instance(obj: Any) -> None:
    for column in TEXT_COLUMNS.get(type(obj), ()):
        value = getattr(obj, column, None)
        if isinstance(value, str) and value:
            cleaned = redact_text(value)
            if cleaned != value:
                setattr(obj, column, cleaned)
    for column in JSON_COLUMNS.get(type(obj), ()):
        value = getattr(obj, column, None)
        if value:
            cleaned = redact(value)
            if cleaned != value:
                setattr(obj, column, cleaned)


@event.listens_for(Session, "before_flush")
def _before_flush(session: Session, _context: Any, _instances: Any) -> None:
    for obj in (*session.new, *session.dirty):
        if type(obj) in TEXT_COLUMNS or type(obj) in JSON_COLUMNS:
            _redact_instance(obj)
