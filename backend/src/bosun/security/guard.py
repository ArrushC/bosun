"""Request guard for a localhost application that can launch code-executing agents.

Defends against:
* DNS rebinding — the Host header must be a loopback name (or explicitly allowed).
* Cross-site request forgery — browsers attach an Origin header to cross-origin
  requests; mutating requests and WebSocket upgrades must come from an allowed
  origin, and mutating requests must use a JSON body (forcing a CORS preflight,
  which this app never approves for foreign origins).
* Remote access — when bound to a non-loopback interface a bearer token is required.
"""

from __future__ import annotations

import hmac
from collections.abc import Iterable
from urllib.parse import urlsplit

from starlette.types import ASGIApp, Receive, Scope, Send

LOOPBACK_HOSTS = {"localhost", "127.0.0.1", "::1", "[::1]"}
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


def _hostname(host_header: str) -> str:
    host = host_header.strip().lower()
    if host.startswith("["):
        return host.split("]")[0] + "]"
    return host.rsplit(":", 1)[0] if host.count(":") == 1 else host


class LocalRequestGuard:
    def __init__(
        self,
        app: ASGIApp,
        *,
        allowed_origins: Iterable[str],
        extra_hosts: Iterable[str] = (),
        auth_token: str | None = None,
    ) -> None:
        self.app = app
        self.allowed_origins = {o.rstrip("/").lower() for o in allowed_origins}
        self.allowed_hosts = LOOPBACK_HOSTS | {h.lower() for h in extra_hosts}
        self.auth_token = auth_token

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return

        headers = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope["headers"]}
        problem = self._check(scope, headers)
        if problem is None:
            await self.app(scope, receive, send)
            return

        if scope["type"] == "websocket":
            await send({"type": "websocket.close", "code": 4403, "reason": problem})
            return
        body = ('{"detail":"' + problem + '"}').encode()
        await send(
            {
                "type": "http.response.start",
                "status": 403,
                "headers": [(b"content-type", b"application/json")],
            }
        )
        await send({"type": "http.response.body", "body": body})

    def _check(self, scope: Scope, headers: dict[str, str]) -> str | None:
        host = _hostname(headers.get("host", ""))
        if host not in self.allowed_hosts:
            return f"Host '{host}' is not allowed"

        if self.auth_token:
            supplied = headers.get("authorization", "").removeprefix("Bearer ").strip()
            if not supplied and scope["type"] == "websocket":
                supplied = _query_param(scope, "token")
            if not hmac.compare_digest(supplied.encode(), self.auth_token.encode()):
                return "Missing or invalid auth token"

        origin = headers.get("origin")
        is_mutation = scope["type"] == "websocket" or scope.get("method", "GET") not in SAFE_METHODS
        if origin and is_mutation and not self._origin_allowed(origin, headers):
            return f"Origin '{origin}' is not allowed"

        if scope["type"] == "http" and scope.get("method") not in SAFE_METHODS:
            content_type = headers.get("content-type", "")
            length = headers.get("content-length", "0")
            has_body = length not in ("", "0") or "transfer-encoding" in headers
            if has_body and not content_type.startswith("application/json"):
                return "Mutating requests must use application/json"
        return None

    def _origin_allowed(self, origin: str, headers: dict[str, str]) -> bool:
        origin = origin.rstrip("/").lower()
        if origin in self.allowed_origins:
            return True
        # Same-origin requests from the UI served by this backend.
        parts = urlsplit(origin)
        return (
            parts.netloc == headers.get("host", "").lower()
            and _hostname(parts.netloc) in self.allowed_hosts
        )


def _query_param(scope: Scope, name: str) -> str:
    from urllib.parse import parse_qs

    params = parse_qs(scope.get("query_string", b"").decode())
    values = params.get(name)
    return values[0] if values else ""
