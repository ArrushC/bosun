"""GitHub source synchronisation against a mocked REST API."""

from __future__ import annotations

import time
from typing import Any

import httpx
from sqlalchemy import select

from bosun.db.models import SourceConnection, SourceRecord, Task
from bosun.domain.enums import ConnectionStatus, SourceKind, SourceRecordState, TaskKind
from bosun.ingestion.github.client import GitHubClient, GitHubRateLimited
from bosun.ingestion.github.connector import GitHubConnectionConfig
from bosun.services import Services
from bosun.tasks.service import import_records

BASE = "https://api.github.test"


def issue(
    number: int,
    title: str,
    *,
    state: str = "open",
    labels: list[str] | None = None,
    pr: bool = False,
    merged: bool = False,
    updated: str = "2026-10-01T10:00:00Z",
) -> dict[str, Any]:
    data: dict[str, Any] = {
        "number": number,
        "title": title,
        "body": f"Body of {title}",
        "state": state,
        "html_url": f"https://github.com/acme/api/issues/{number}",
        "user": {"login": "octo"},
        "labels": [{"name": n} for n in labels or []],
        "assignees": [],
        "milestone": None,
        "comments": 2,
        "created_at": "2026-09-01T10:00:00Z",
        "updated_at": updated,
        "closed_at": None,
    }
    if pr:
        data["pull_request"] = {"merged_at": "2026-10-02T00:00:00Z" if merged else None}
    return data


class FakeGitHub:
    def __init__(self) -> None:
        self.issues: dict[int, dict[str, Any]] = {
            1: issue(1, "Crash on empty cart", labels=["bug"]),
            2: issue(2, "Support coupons"),
            3: issue(3, "Add caching layer", pr=True),
            4: issue(4, "Document the API"),
        }
        self.deleted: set[int] = set()
        self.requests: list[httpx.Request] = []
        self.rate_limited = False

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        path = request.url.path
        headers = {
            "x-ratelimit-remaining": "4999",
            "x-ratelimit-limit": "5000",
            "x-ratelimit-reset": str(int(time.time()) + 3600),
        }
        if request.headers.get("authorization") != "Bearer test-token":
            return httpx.Response(401, json={"message": "Bad credentials"})
        if self.rate_limited:
            return httpx.Response(
                403,
                json={"message": "API rate limit exceeded"},
                headers={
                    **headers,
                    "x-ratelimit-remaining": "0",
                    "x-ratelimit-reset": str(int(time.time()) + 600),
                },
            )
        if path == "/user":
            return httpx.Response(
                200,
                json={"login": "octo", "name": "Octo Cat"},
                headers={**headers, "x-oauth-scopes": "repo, read:org"},
            )
        if path == "/repos/acme/api/pulls":
            prs = [
                {
                    "number": n,
                    "draft": False,
                    "head": {"ref": "feature/cache"},
                    "base": {"ref": "main"},
                    "requested_reviewers": [],
                }
                for n, i in self.issues.items()
                if "pull_request" in i and i["state"] == "open"
            ]
            return httpx.Response(200, json=prs, headers=headers)
        if path == "/repos/acme/api/milestones":
            return httpx.Response(200, json=[{"number": 7, "title": "v1.0"}], headers=headers)
        if path == "/repos/acme/api/issues":
            items = [
                i
                for n, i in sorted(self.issues.items())
                if i["state"] == "open" and n not in self.deleted
            ]
            page = int(request.url.params.get("page", "1"))
            chunk = items[(page - 1) * 2 : page * 2]
            link = {}
            if page * 2 < len(items):
                link = {
                    "link": f'<{BASE}/repos/acme/api/issues?state=open&per_page=100&page={page + 1}>; rel="next"'
                }
            return httpx.Response(200, json=chunk, headers={**headers, **link})
        if path.startswith("/repos/acme/api/issues/"):
            number = int(path.rsplit("/", 1)[-1])
            if number in self.deleted or number not in self.issues:
                return httpx.Response(404, json={"message": "Not Found"}, headers=headers)
            return httpx.Response(200, json=self.issues[number], headers=headers)
        return httpx.Response(404, json={"message": f"unexpected {path}"})


async def _connection(services: Services, **config: Any) -> str:
    cfg = GitHubConnectionConfig(repos=["acme/api"], **config)
    async with services.db.session() as session:
        connection = SourceConnection(kind="github", name="acme/api", config=cfg.model_dump())
        session.add(connection)
        await session.commit()
        return connection.id


async def _records(services: Services, connection_id: str) -> dict[int, SourceRecord]:
    async with services.db.session() as session:
        rows = (
            await session.scalars(
                select(SourceRecord).where(SourceRecord.connection_id == connection_id)
            )
        ).all()
    return {r.number or 0: r for r in rows}


async def test_initial_sync_imports_issues_and_prs_with_pagination(services: Services) -> None:
    gh = FakeGitHub()
    services.ingestion.github_transport = httpx.MockTransport(gh.handler)
    conn = await _connection(services)
    result = await services.ingestion.sync_connection(conn)
    assert result["status"] == ConnectionStatus.OK, result
    records = await _records(services, conn)
    assert set(records) == {1, 2, 3, 4}
    assert records[3].kind == SourceKind.GITHUB_PR and records[3].meta["head"] == "feature/cache"
    assert records[1].kind == SourceKind.GITHUB_ISSUE and records[1].labels == ["bug"]
    assert records[1].url == "https://github.com/acme/api/issues/1"
    issue_pages = [r for r in gh.requests if r.url.path == "/repos/acme/api/issues"]
    assert len(issue_pages) == 2  # followed the Link header
    assert all(r.method == "GET" for r in gh.requests)  # read-only ingestion


async def test_resync_updates_closes_deletes_and_deduplicates(services: Services) -> None:
    gh = FakeGitHub()
    services.ingestion.github_transport = httpx.MockTransport(gh.handler)
    conn = await _connection(services)
    await services.ingestion.sync_connection(conn)
    first = await _records(services, conn)

    # Promote issue 2 to a task so its closure is reflected on the board.
    async with services.db.session() as session:
        record = await session.get(SourceRecord, first[2].id)
        [task] = await import_records(session, [record])  # type: ignore[list-item]
        await session.commit()
        task_id = task.id

    gh.issues[1]["title"] = "Crash on empty cart (iOS)"
    gh.issues[2]["state"] = "closed"
    gh.issues[3]["state"] = "closed"
    gh.issues[3]["pull_request"]["merged_at"] = "2026-10-03T00:00:00Z"
    gh.deleted.add(4)
    result = await services.ingestion.sync_connection(conn)
    assert result["status"] == ConnectionStatus.OK, result

    records = await _records(services, conn)
    assert len(records) == 4  # no duplicates
    assert records[1].id == first[1].id and records[1].title == "Crash on empty cart (iOS)"
    assert records[2].state == SourceRecordState.CLOSED
    assert records[3].state == SourceRecordState.MERGED
    assert records[4].state == SourceRecordState.DELETED
    async with services.db.session() as session:
        task = await session.get(Task, task_id)
    assert task is not None and task.source_state == SourceRecordState.CLOSED
    assert "closed" in (task.last_activity or "").lower()


async def test_issue_kind_suggestions(services: Services) -> None:
    gh = FakeGitHub()
    services.ingestion.github_transport = httpx.MockTransport(gh.handler)
    conn = await _connection(services)
    await services.ingestion.sync_connection(conn)
    records = await _records(services, conn)
    async with services.db.session() as session:
        tasks = await import_records(
            session, [await session.get(SourceRecord, records[n].id) for n in (1, 2, 3)]
        )  # type: ignore[misc]
        await session.commit()
    kinds = [t.kind for t in tasks]
    assert kinds == [TaskKind.BUGFIX, TaskKind.IMPLEMENTATION, TaskKind.REVIEW]


async def test_filters_are_sent_and_milestone_resolved(services: Services) -> None:
    gh = FakeGitHub()
    services.ingestion.github_transport = httpx.MockTransport(gh.handler)
    conn = await _connection(
        services,
        filters={
            "labels": ["bug", "p1"],
            "author": "octo",
            "assignee": "*",
            "milestone": "v1.0",
            "include_prs": False,
        },
    )
    await services.ingestion.sync_connection(conn)
    issues_req = next(r for r in gh.requests if r.url.path == "/repos/acme/api/issues")
    params = issues_req.url.params
    assert (
        params["labels"] == "bug,p1" and params["creator"] == "octo" and params["assignee"] == "*"
    )
    assert params["milestone"] == "7"
    assert not any(r.url.path.endswith("/pulls") for r in gh.requests)
    records = await _records(services, conn)
    assert all(r.kind == SourceKind.GITHUB_ISSUE for r in records.values())


async def test_rate_limit_marks_connection_and_keeps_data(services: Services) -> None:
    gh = FakeGitHub()
    services.ingestion.github_transport = httpx.MockTransport(gh.handler)
    conn = await _connection(services)
    await services.ingestion.sync_connection(conn)
    gh.rate_limited = True
    result = await services.ingestion.sync_connection(conn)
    assert result["status"] == ConnectionStatus.RATE_LIMITED
    async with services.db.session() as session:
        connection = await session.get(SourceConnection, conn)
    assert connection is not None and connection.rate_limit_reset_at is not None
    assert "rate limit" in (connection.last_error or "").lower()
    records = await _records(services, conn)
    assert all(
        r.state == SourceRecordState.OPEN for r in records.values()
    )  # nothing wrongly closed


async def test_auth_failure_is_actionable(services: Services, settings: Any) -> None:
    gh = FakeGitHub()
    services.ingestion.github_transport = httpx.MockTransport(gh.handler)
    settings.github_token = "wrong"
    conn = await _connection(services)
    result = await services.ingestion.sync_connection(conn)
    assert result["status"] == ConnectionStatus.ERROR
    assert "gh auth login" in result["error"]


async def test_connectivity_failure_is_reported(services: Services) -> None:
    def offline(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("network down", request=request)

    services.ingestion.github_transport = httpx.MockTransport(offline)
    conn = await _connection(services)
    original = GitHubClient.__init__

    def fast_retry(self, *args, **kwargs):  # noqa: ANN001, ANN002, ANN003
        kwargs["retry_base_delay"] = 0
        original(self, *args, **kwargs)

    GitHubClient.__init__ = fast_retry  # type: ignore[method-assign]
    try:
        result = await services.ingestion.sync_connection(conn)
    finally:
        GitHubClient.__init__ = original  # type: ignore[method-assign]
    assert result["status"] == ConnectionStatus.ERROR
    assert "network" in result["error"].lower()


async def test_secondary_rate_limit_uses_retry_after() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            403, json={"message": "secondary rate limit"}, headers={"retry-after": "30"}
        )

    async with GitHubClient("t", BASE, transport=httpx.MockTransport(handler)) as client:
        try:
            await client.viewer()
        except GitHubRateLimited as exc:
            assert 20 < (exc.reset_at.timestamp() - time.time()) <= 31
        else:
            raise AssertionError("expected rate limit")
