"""WebSocket channel for live execution events and entity change notifications.

Protocol (JSON messages):

client → server
    {"type": "subscribe", "topics": ["entities", "runs", "run:<id>"]}
    {"type": "replay", "run_id": "<id>", "after_seq": 120}   # catch up after reconnecting
    {"type": "ping"}

server → client
    {"kind": "hello", "last_seq": <global last seq>}
    {"kind": "event", "event": {...}}            # persisted execution event (ordered by seq)
    {"kind": "entity", "entity": "task", "id": "...", "action": "updated"}
    {"kind": "replay_done", "run_id": "...", "last_seq": 150}
    {"kind": "resync"}                           # client fell behind: refetch everything
    {"kind": "ping"} / {"kind": "pong"}

Events are persisted before they are broadcast, so a client that reconnects and
replays from its last seen `seq` never misses one. Clients de-duplicate by `seq`.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from sqlalchemy import func, select

from bosun.db.models import ExecutionEvent

log = logging.getLogger(__name__)
router = APIRouter()

HEARTBEAT_S = 20.0


@router.websocket("/ws")
async def websocket(ws: WebSocket) -> None:
    services = ws.app.state.services
    await ws.accept()
    sub = services.bus.subscribe()
    sub.topics = {"entities", "runs"}
    async with services.db.session() as session:
        last_seq = await session.scalar(select(func.max(ExecutionEvent.seq))) or 0
    await ws.send_json({"kind": "hello", "last_seq": last_seq})

    async def pump() -> None:
        while True:
            try:
                message = await asyncio.wait_for(sub.queue.get(), HEARTBEAT_S)
            except TimeoutError:
                await ws.send_json({"kind": "ping"})
                continue
            if sub.overflowed:
                while not sub.queue.empty():
                    sub.queue.get_nowait()
                sub.overflowed = False
                await ws.send_json({"kind": "resync"})
                continue
            await ws.send_text(json.dumps(message, default=str))

    pump_task = asyncio.create_task(pump())
    try:
        while True:
            raw = await ws.receive_text()
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                continue
            kind = msg.get("type")
            if kind == "subscribe":
                topics = {str(t) for t in msg.get("topics", [])}
                sub.topics = topics or {"entities", "runs"}
            elif kind == "replay":
                run_id = str(msg.get("run_id", ""))
                after = int(msg.get("after_seq") or 0)
                last = after
                while True:
                    rows = await services.bus.history(run_id=run_id, after_seq=last, limit=1000)
                    for row in rows:
                        await ws.send_text(
                            json.dumps({"kind": "event", "event": row, "replay": True}, default=str)
                        )
                    if rows:
                        last = rows[-1]["seq"]
                    if len(rows) < 1000:
                        break
                await ws.send_json({"kind": "replay_done", "run_id": run_id, "last_seq": last})
            elif kind == "ping":
                await ws.send_json({"kind": "pong"})
    except WebSocketDisconnect:
        pass
    except Exception:  # noqa: BLE001
        log.exception("WebSocket error")
    finally:
        pump_task.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await pump_task
        services.bus.unsubscribe(sub)
