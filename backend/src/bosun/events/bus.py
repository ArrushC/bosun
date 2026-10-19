"""Event bus: execution events are persisted first, then broadcast.

Two kinds of messages flow through the bus:

* **Execution events** (`ExecutionEvent` rows) — durable, ordered by `seq`,
  replayable. Clients reconnecting with `since_seq` receive anything missed.
* **Entity notifications** — ephemeral "task X changed" hints that let the UI
  invalidate cached queries. They are not persisted; clients refetch on reconnect.

Persistence and publication happen under one lock so subscribers always
observe events in `seq` order.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from sqlalchemy import select

from bosun.db.models import ExecutionEvent
from bosun.db.session import Database
from bosun.ids import new_id, utcnow
from bosun.security.redaction import redact

log = logging.getLogger(__name__)

MAX_EVENT_STRING = 8_000


@dataclass(slots=True)
class EventInput:
    run_id: str
    type: str
    task_id: str | None = None
    session_id: str | None = None
    agent_id: str | None = None
    parent_agent_id: str | None = None
    status: str | None = None
    data: dict[str, Any] = field(default_factory=dict)
    ts: datetime | None = None


def event_to_dict(event: ExecutionEvent) -> dict[str, Any]:
    return {
        "seq": event.seq,
        "id": event.id,
        "run_id": event.run_id,
        "task_id": event.task_id,
        "session_id": event.session_id,
        "agent_id": event.agent_id,
        "parent_agent_id": event.parent_agent_id,
        "type": event.type,
        "status": event.status,
        "ts": event.ts.isoformat(),
        "data": event.data,
    }


class Subscriber:
    """One connected client. Bounded queue; overflow forces a client resync."""

    def __init__(self, maxsize: int = 2_000) -> None:
        self.queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=maxsize)
        self.topics: set[str] = set()
        self.overflowed = False

    def wants(self, message: dict[str, Any]) -> bool:
        if "*" in self.topics:
            return True
        if message["kind"] == "entity":
            return "entities" in self.topics
        event = message["event"]
        return "runs" in self.topics or f"run:{event['run_id']}" in self.topics

    def offer(self, message: dict[str, Any]) -> None:
        if self.overflowed:
            return
        try:
            self.queue.put_nowait(message)
        except asyncio.QueueFull:
            self.overflowed = True
            log.warning("WebSocket subscriber overflowed; forcing resync")


class EventBus:
    def __init__(self, db: Database) -> None:
        self._db = db
        self._lock = asyncio.Lock()
        self._subscribers: set[Subscriber] = set()

    # -- subscriptions --------------------------------------------------------
    def subscribe(self) -> Subscriber:
        sub = Subscriber()
        self._subscribers.add(sub)
        return sub

    def unsubscribe(self, sub: Subscriber) -> None:
        self._subscribers.discard(sub)

    @property
    def subscriber_count(self) -> int:
        return len(self._subscribers)

    def _publish(self, message: dict[str, Any]) -> None:
        for sub in list(self._subscribers):
            if sub.wants(message):
                sub.offer(message)

    # -- execution events -----------------------------------------------------
    async def emit(self, event: EventInput) -> dict[str, Any]:
        return (await self.emit_many([event]))[0]

    async def emit_many(self, events: Iterable[EventInput]) -> list[dict[str, Any]]:
        rows: list[ExecutionEvent] = []
        async with self._lock:
            async with self._db.session() as session:
                for ev in events:
                    row = ExecutionEvent(
                        id=new_id("evt"),
                        run_id=ev.run_id,
                        task_id=ev.task_id,
                        session_id=ev.session_id,
                        agent_id=ev.agent_id,
                        parent_agent_id=ev.parent_agent_id,
                        type=ev.type,
                        status=ev.status,
                        ts=ev.ts or utcnow(),
                        data=redact(ev.data, max_string=MAX_EVENT_STRING),
                    )
                    session.add(row)
                    rows.append(row)
                await session.commit()
            payloads = [event_to_dict(row) for row in rows]
            for payload in payloads:
                self._publish({"kind": "event", "event": payload})
        return payloads

    async def history(
        self, *, run_id: str | None = None, after_seq: int = 0, limit: int = 2_000
    ) -> list[dict[str, Any]]:
        async with self._db.session() as session:
            stmt = select(ExecutionEvent).where(ExecutionEvent.seq > after_seq)
            if run_id is not None:
                stmt = stmt.where(ExecutionEvent.run_id == run_id)
            stmt = stmt.order_by(ExecutionEvent.seq).limit(limit)
            rows = (await session.scalars(stmt)).all()
            return [event_to_dict(row) for row in rows]

    # -- entity notifications -------------------------------------------------
    def notify(
        self, entity: str, entity_id: str | None = None, action: str = "updated", **extra: Any
    ) -> None:
        self._publish(
            {
                "kind": "entity",
                "entity": entity,
                "id": entity_id,
                "action": action,
                "ts": utcnow().isoformat(),
                **extra,
            }
        )
