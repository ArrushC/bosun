"""Read-only GitHub REST client.

Only GET requests are exposed: GitHub ingestion is read-only by construction.
Handles Link-header pagination, primary and secondary rate limits, transient
failures with bounded retries, and connectivity errors.
"""

from __future__ import annotations

import asyncio
import logging
import re
import shutil
import subprocess
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx

log = logging.getLogger(__name__)

_LINK_NEXT = re.compile(r'<([^>]+)>;\s*rel="next"')


class GitHubError(Exception):
    def __init__(self, message: str, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


class GitHubAuthError(GitHubError):
    pass


class GitHubRateLimited(GitHubError):
    def __init__(self, reset_at: datetime, message: str = "GitHub rate limit reached") -> None:
        super().__init__(f"{message}; resets at {reset_at.isoformat()}", status=429)
        self.reset_at = reset_at


class GitHubUnavailable(GitHubError):
    pass


@dataclass
class RateLimitState:
    limit: int | None = None
    remaining: int | None = None
    reset_at: datetime | None = None


def resolve_token(explicit: str | None) -> tuple[str | None, str]:
    """Return (token, source). Never logs or persists the token."""
    if explicit:
        return explicit, "BOSUN_GITHUB_TOKEN"
    gh = shutil.which("gh")
    if gh:
        try:
            result = subprocess.run(
                [gh, "auth", "token"], capture_output=True, text=True, timeout=15, check=False
            )
        except (OSError, subprocess.TimeoutExpired):
            return None, "gh-error"
        token = result.stdout.strip()
        if result.returncode == 0 and token:
            return token, "gh"
        return None, "gh-not-logged-in"
    return None, "none"


class GitHubClient:
    def __init__(
        self,
        token: str | None,
        base_url: str = "https://api.github.com",
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        max_retries: int = 2,
        retry_base_delay: float = 1.0,
    ) -> None:
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "bosun",
        }
        if token:
            headers["Authorization"] = f"Bearer {token}"
        self._client = httpx.AsyncClient(
            base_url=base_url, headers=headers, timeout=30.0, transport=transport
        )
        self._max_retries = max_retries
        self._retry_base_delay = retry_base_delay
        self.rate_limit = RateLimitState()
        self.scopes: list[str] = []

    async def aclose(self) -> None:
        await self._client.aclose()

    async def __aenter__(self) -> GitHubClient:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.aclose()

    def _track_rate_limit(self, response: httpx.Response) -> None:
        h = response.headers
        if "x-ratelimit-remaining" in h:
            self.rate_limit.remaining = int(h["x-ratelimit-remaining"])
            self.rate_limit.limit = int(h.get("x-ratelimit-limit", "0") or 0)
            self.rate_limit.reset_at = datetime.fromtimestamp(
                int(h.get("x-ratelimit-reset", "0")), UTC
            )
        if "x-oauth-scopes" in h:
            self.scopes = [s.strip() for s in h["x-oauth-scopes"].split(",") if s.strip()]

    def _rate_limit_error(self, response: httpx.Response) -> GitHubRateLimited | None:
        if response.status_code not in (403, 429):
            return None
        retry_after = response.headers.get("retry-after")
        if retry_after and retry_after.isdigit():
            return GitHubRateLimited(
                datetime.now(UTC) + timedelta(seconds=int(retry_after)),
                "GitHub secondary rate limit",
            )
        if response.headers.get("x-ratelimit-remaining") == "0":
            reset = int(response.headers.get("x-ratelimit-reset", "0") or 0)
            return GitHubRateLimited(datetime.fromtimestamp(reset, UTC))
        body = response.text.lower()
        if "rate limit" in body:
            return GitHubRateLimited(datetime.now(UTC) + timedelta(seconds=60))
        return None

    async def _get(self, url: str, params: dict[str, Any] | None = None) -> httpx.Response:
        attempt = 0
        while True:
            try:
                response = await self._client.get(url, params=params)
            except httpx.TransportError as exc:
                if attempt >= self._max_retries:
                    raise GitHubUnavailable(f"Cannot reach GitHub: {exc}") from exc
                attempt += 1
                await asyncio.sleep(self._retry_base_delay * 2 ** (attempt - 1))
                continue
            self._track_rate_limit(response)
            if (limited := self._rate_limit_error(response)) is not None:
                raise limited
            if response.status_code == 401:
                raise GitHubAuthError("GitHub rejected the credentials (401)", 401)
            if response.status_code >= 500 and attempt < self._max_retries:
                attempt += 1
                await asyncio.sleep(self._retry_base_delay * 2 ** (attempt - 1))
                continue
            return response

    async def get_json(self, url: str, params: dict[str, Any] | None = None) -> Any:
        response = await self._get(url, params)
        if response.status_code >= 400:
            raise GitHubError(
                f"GitHub {response.status_code} for {url}: {response.text[:200]}",
                response.status_code,
            )
        return response.json()

    async def paginate(
        self, url: str, params: dict[str, Any] | None = None, *, max_pages: int = 30
    ) -> AsyncIterator[dict[str, Any]]:
        next_url: str | None = url
        next_params = {"per_page": 100, **(params or {})}
        pages = 0
        while next_url and pages < max_pages:
            response = await self._get(next_url, next_params)
            if response.status_code >= 400:
                raise GitHubError(
                    f"GitHub {response.status_code} for {next_url}: {response.text[:200]}",
                    response.status_code,
                )
            data = response.json()
            items = data.get("items", []) if isinstance(data, dict) else data
            for item in items:
                yield item
            pages += 1
            match = _LINK_NEXT.search(response.headers.get("link", ""))
            next_url = match.group(1) if match else None
            next_params = None  # the next link already carries the query string

    # -- endpoints -------------------------------------------------------------
    async def viewer(self) -> dict[str, Any]:
        return await self.get_json("/user")

    async def list_repos(self, query: str | None = None, limit: int = 300) -> list[dict[str, Any]]:
        repos: list[dict[str, Any]] = []
        params = {"sort": "pushed", "affiliation": "owner,collaborator,organization_member"}
        async for repo in self.paginate("/user/repos", params, max_pages=max(1, limit // 100)):
            if query and query.lower() not in repo["full_name"].lower():
                continue
            repos.append(repo)
            if len(repos) >= limit:
                break
        return repos

    async def get_repo(self, full_name: str) -> dict[str, Any]:
        return await self.get_json(f"/repos/{full_name}")

    def list_issues(
        self,
        full_name: str,
        *,
        state: str = "open",
        labels: list[str] | None = None,
        milestone: str | None = None,
        creator: str | None = None,
        assignee: str | None = None,
        since: datetime | None = None,
        max_pages: int = 30,
    ) -> AsyncIterator[dict[str, Any]]:
        params: dict[str, Any] = {"state": state, "sort": "updated", "direction": "desc"}
        if labels:
            params["labels"] = ",".join(labels)
        if milestone:
            params["milestone"] = milestone
        if creator:
            params["creator"] = creator
        if assignee:
            params["assignee"] = assignee
        if since:
            params["since"] = since.astimezone(UTC).isoformat().replace("+00:00", "Z")
        return self.paginate(f"/repos/{full_name}/issues", params, max_pages=max_pages)

    def list_pulls(
        self, full_name: str, *, state: str = "open", max_pages: int = 10
    ) -> AsyncIterator[dict[str, Any]]:
        return self.paginate(f"/repos/{full_name}/pulls", {"state": state}, max_pages=max_pages)

    async def list_milestones(self, full_name: str) -> list[dict[str, Any]]:
        return [
            m
            async for m in self.paginate(
                f"/repos/{full_name}/milestones", {"state": "all"}, max_pages=3
            )
        ]

    async def list_labels(self, full_name: str) -> list[dict[str, Any]]:
        return [label async for label in self.paginate(f"/repos/{full_name}/labels", max_pages=3)]

    async def get_issue(self, full_name: str, number: int) -> dict[str, Any] | None:
        """Returns None when the issue was deleted (404/410)."""
        response = await self._get(f"/repos/{full_name}/issues/{number}")
        if response.status_code in (404, 410):
            return None
        if response.status_code >= 400:
            raise GitHubError(
                f"GitHub {response.status_code} for issue {number}", response.status_code
            )
        return response.json()
