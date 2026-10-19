"""Secret redaction for logs, persisted events and API payloads.

Redaction is applied *before* data is persisted or broadcast, so secrets that
appear in tool inputs/outputs never reach the database or the browser.
"""

from __future__ import annotations

import re
from typing import Any

REDACTED = "[REDACTED]"

_PATTERNS: list[re.Pattern[str]] = [
    # GitHub tokens
    re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{20,255}|github_pat_[A-Za-z0-9_]{20,255})\b"),
    # Anthropic keys / OAuth tokens
    re.compile(r"\bsk-ant-[A-Za-z0-9_\-]{10,}\b"),
    # OpenAI-style and generic `sk-` keys
    re.compile(r"\bsk-[A-Za-z0-9]{32,}\b"),
    # AWS access key ids
    re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"),
    # Google API keys
    re.compile(r"\bAIza[0-9A-Za-z_\-]{35}\b"),
    # Slack tokens
    re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}\b"),
    # JWTs
    re.compile(r"\beyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\b"),
    # Private key blocks
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----"),
    # Bearer and Basic authorization values
    re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/\-]{16,}=*"),
    re.compile(r"(?i)\bbasic\s+[A-Za-z0-9+/]{12,}={0,2}"),
]

# Credentials embedded in URLs: scheme://user:password@host
_URL_CREDENTIALS = re.compile(r"(?i)(\b[a-z][a-z0-9+.\-]*://[^/\s:@\"']+:)[^/\s@\"']+@")

# Command-line flags that take a secret: --password value, --token=value
_SECRET_FLAG = re.compile(
    r"(?i)(--(?:password|passwd|pass|token|api[-_]?key|secret|client[-_]secret|auth[-_]token)(?:=|\s+))"
    r"(?!\[REDACTED\])(\"[^\"]*\"|'[^']*'|\S+)"
)

_SECRET_WORDS = (
    r"api[_-]?key|secret|token|passwd|password|private[_-]?key|access[_-]?key|client[_-]?secret"
)

# key=value / "key": "value" assignments with secret-looking key names. Quoted
# values may contain spaces; bare values must look like data, not code.
_ASSIGNMENT = re.compile(
    rf"""(?ix)
    (?P<key>\b[\w.\-]*(?:{_SECRET_WORDS})[\w.\-]*)
    (?P<sep>["']?\s*[:=]\s*)
    (?:
        (?P<q>["'])(?P<quoted>[^"'\n]{{3,}})(?P=q)
      | (?P<value>[^\s"',;)\]}}]{{4,}})
    )
    """
)

_SECRET_KEY_NAMES = re.compile(
    r"(?i)(api[_-]?key|secret|token|passw(or)?d|private[_-]?key|access[_-]?key|authorization|cookie)"
)
# Key names that contain a secret word but describe metadata, not a secret.
_NOT_SECRET_KEYS = re.compile(
    r"(?i)(?:source|kind|status|type|method|mode|provider|present|configured|count|"
    r"tokens|expires(?:_at)?|url|name|id|ids|env|header|limit|budget|usage)$"
)
# Bare assignment values that are code or placeholders rather than secrets.
_NOT_SECRET_VALUES = re.compile(
    r"(?i)^(?:none|null|nil|true|false|undefined|str|string|int|bool|bytes|optional\[.*|"
    r"\$\{?[\w.]+\}?|os\.environ.*|os\.getenv.*|process\.env.*|getenv.*|env\[.*|<[^>]*>|"
    r"\[redacted.*|\*+|x{3,}|\.{3}|…|self\..*|settings\..*|config\..*|args\..*)$"
)


def _is_secret_key(key: str) -> bool:
    return bool(_SECRET_KEY_NAMES.search(key)) and not _NOT_SECRET_KEYS.search(key)


def _assignment(m: re.Match[str]) -> str:
    key = m.group("key")
    if _NOT_SECRET_KEYS.search(key):
        return m.group(0)
    if m.group("q"):
        if _NOT_SECRET_VALUES.match(m.group("quoted")):
            return m.group(0)
        return f"{key}{m.group('sep')}{m.group('q')}{REDACTED}{m.group('q')}"
    if _NOT_SECRET_VALUES.match(m.group("value")):
        return m.group(0)
    return f"{key}{m.group('sep')}{REDACTED}"


def redact_text(text: str) -> str:
    if not text:
        return text
    for pattern in _PATTERNS:
        text = pattern.sub(REDACTED, text)
    text = _URL_CREDENTIALS.sub(rf"\1{REDACTED}@", text)
    text = _SECRET_FLAG.sub(rf"\1{REDACTED}", text)
    return _ASSIGNMENT.sub(_assignment, text)


def redact(value: Any, *, max_string: int | None = None, _secret: bool = False) -> Any:
    """Recursively redact strings in JSON-like data; mask values of secret-named keys.

    Everything under a secret-named key (including nested lists and objects) has
    its string values masked.
    """
    if isinstance(value, str):
        if _secret and value:
            return REDACTED
        out = redact_text(value)
        if max_string is not None and len(out) > max_string:
            out = f"{out[:max_string]}… [truncated {len(out) - max_string} chars]"
        return out
    if isinstance(value, dict):
        return {
            key: redact(
                item,
                max_string=max_string,
                _secret=_secret or (isinstance(key, str) and _is_secret_key(key)),
            )
            for key, item in value.items()
        }
    if isinstance(value, list | tuple):
        return [redact(item, max_string=max_string, _secret=_secret) for item in value]
    return value
