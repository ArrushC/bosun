"""Prefixed, sortable-enough identifiers (e.g. `run_8f3a91c2d4e5b6a7`)."""

from __future__ import annotations

import secrets
from datetime import UTC, datetime


def new_id(prefix: str) -> str:
    return f"{prefix}_{secrets.token_hex(8)}"


def utcnow() -> datetime:
    return datetime.now(UTC)
