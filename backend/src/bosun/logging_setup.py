"""Structured logging with secret redaction applied to every record."""

from __future__ import annotations

import json
import logging
import sys
from datetime import UTC, datetime

from bosun.security.redaction import redact_text


class _RedactingFormatter(logging.Formatter):
    def __init__(self, as_json: bool) -> None:
        super().__init__()
        self._as_json = as_json

    def format(self, record: logging.LogRecord) -> str:
        message = redact_text(record.getMessage())
        extra = {k: v for k, v in record.__dict__.items() if k.startswith("ctx_")}
        if self._as_json:
            payload = {
                "ts": datetime.fromtimestamp(record.created, UTC).isoformat(),
                "level": record.levelname.lower(),
                "logger": record.name,
                "msg": message,
                **{k.removeprefix("ctx_"): v for k, v in extra.items()},
            }
            if record.exc_info:
                payload["exc"] = redact_text(self.formatException(record.exc_info))
            return json.dumps(payload, default=str)
        ctx = " ".join(f"{k.removeprefix('ctx_')}={v}" for k, v in extra.items())
        line = f"{datetime.fromtimestamp(record.created).strftime('%H:%M:%S')} {record.levelname:<7} {record.name}: {message}"
        if ctx:
            line = f"{line}  [{redact_text(ctx)}]"
        if record.exc_info:
            line = f"{line}\n{redact_text(self.formatException(record.exc_info))}"
        return line


def configure_logging(level: str = "INFO", as_json: bool = False) -> None:
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(_RedactingFormatter(as_json))
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level.upper())
    for noisy in ("uvicorn.access", "httpx", "httpcore", "aiosqlite", "sqlalchemy.engine"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def log_context(**ctx: object) -> dict[str, object]:
    """Build `extra=` for a log call: `log.info("...", extra=log_context(run_id=...))`."""
    return {f"ctx_{k}": v for k, v in ctx.items()}
