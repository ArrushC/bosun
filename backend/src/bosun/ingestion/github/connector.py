"""GitHub issues and pull requests as a `SourceConnector`."""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from bosun.domain.enums import SourceKind, SourceRecordState
from bosun.ingestion.base import CollectResult, ExternalItem, SourceConnector
from bosun.ingestion.github.client import (
    GitHubAuthError,
    GitHubClient,
    GitHubError,
    GitHubRateLimited,
    GitHubUnavailable,
)

log = logging.getLogger(__name__)

MAX_BODY = 20_000
MAX_VERIFY_PER_SYNC = 40


class GitHubFilters(BaseModel):
    state: str = Field(default="open", pattern="^(open|all)$")
    labels: list[str] = Field(default_factory=list)
    milestone: str | None = None  # milestone title, "*" (any) or "none"
    author: str | None = None
    assignee: str | None = None  # login, "*" (any) or "none"
    include_issues: bool = True
    include_prs: bool = True


class GitHubConnectionConfig(BaseModel):
    repos: list[str] = Field(default_factory=list)  # "owner/name"
    filters: GitHubFilters = Field(default_factory=GitHubFilters)
    sync_interval_minutes: int = Field(default=0, ge=0, le=1440)  # 0 = manual only
    max_pages_per_repo: int = Field(default=10, ge=1, le=100)


def _parse_time(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value.replace("Z", "+00:00")) if value else None


def issue_state(issue: dict[str, Any]) -> str:
    if issue.get("state") == "open":
        return SourceRecordState.OPEN
    pr = issue.get("pull_request") or {}
    if pr.get("merged_at") or issue.get("merged_at"):
        return SourceRecordState.MERGED
    return SourceRecordState.CLOSED


def issue_to_item(
    repo: str, issue: dict[str, Any], pr_details: dict[int, dict[str, Any]]
) -> ExternalItem:
    number = int(issue["number"])
    is_pr = "pull_request" in issue
    meta: dict[str, object] = {
        "is_pr": is_pr,
        "comments": issue.get("comments", 0),
        "assignees": [a["login"] for a in issue.get("assignees") or []],
        "milestone": (issue.get("milestone") or {}).get("title"),
        "created_at": issue.get("created_at"),
        "closed_at": issue.get("closed_at"),
    }
    if is_pr and number in pr_details:
        pr = pr_details[number]
        meta.update(
            {
                "draft": pr.get("draft", False),
                "head": (pr.get("head") or {}).get("ref"),
                "base": (pr.get("base") or {}).get("ref"),
                "requested_reviewers": [r["login"] for r in pr.get("requested_reviewers") or []],
            }
        )
    body = issue.get("body") or ""
    if len(body) > MAX_BODY:
        body = body[:MAX_BODY] + "\n\n… [truncated]"
    return ExternalItem(
        kind=SourceKind.GITHUB_PR if is_pr else SourceKind.GITHUB_ISSUE,
        external_id=f"{repo}#{number}",
        title=issue.get("title") or f"#{number}",
        body=body,
        url=issue.get("html_url"),
        repo=repo,
        number=number,
        author=(issue.get("user") or {}).get("login"),
        labels=[label["name"] for label in issue.get("labels") or [] if isinstance(label, dict)],
        meta=meta,
        state=issue_state(issue),
        external_updated_at=_parse_time(issue.get("updated_at")),
    )


class GitHubConnector(SourceConnector):
    kind = "github"

    def __init__(
        self,
        connection_id: str,
        config: GitHubConnectionConfig,
        client: GitHubClient,
        *,
        since: datetime | None = None,
    ) -> None:
        self._connection_id = connection_id
        self._config = config
        self._client = client
        self._since = since

    @property
    def scope(self) -> str:
        return self._connection_id

    async def _resolve_milestone(self, repo: str) -> str | None:
        wanted = self._config.filters.milestone
        if not wanted or wanted in ("*", "none") or wanted.isdigit():
            return wanted
        for milestone in await self._client.list_milestones(repo):
            if milestone.get("title", "").lower() == wanted.lower():
                return str(milestone["number"])
        raise GitHubError(f"Milestone '{wanted}' not found in {repo}")

    async def collect(self, known_open: dict[str, int | None]) -> CollectResult:
        filters = self._config.filters
        items: list[ExternalItem] = []
        errors: list[str] = []
        per_repo: dict[str, dict[str, int]] = {}
        completed_repos: set[str] = set()
        rate_limited: GitHubRateLimited | None = None

        for repo in self._config.repos:
            counts = {"issues": 0, "prs": 0}
            try:
                milestone = await self._resolve_milestone(repo)
                pr_details: dict[int, dict[str, Any]] = {}
                if filters.include_prs:
                    async for pr in self._client.list_pulls(
                        repo, state=filters.state, max_pages=self._config.max_pages_per_repo
                    ):
                        pr_details[int(pr["number"])] = pr
                async for issue in self._client.list_issues(
                    repo,
                    state=filters.state,
                    labels=filters.labels,
                    milestone=milestone,
                    creator=filters.author,
                    assignee=filters.assignee,
                    since=self._since if filters.state == "all" else None,
                    max_pages=self._config.max_pages_per_repo,
                ):
                    is_pr = "pull_request" in issue
                    if (is_pr and not filters.include_prs) or (
                        not is_pr and not filters.include_issues
                    ):
                        continue
                    items.append(issue_to_item(repo, issue, pr_details))
                    counts["prs" if is_pr else "issues"] += 1
                completed_repos.add(repo)
            except GitHubRateLimited as exc:
                rate_limited = exc
                errors.append(f"{repo}: {exc}")
                break
            except (GitHubAuthError, GitHubUnavailable):
                raise  # affects every repository: abort the whole sync
            except GitHubError as exc:
                errors.append(f"{repo}: {exc}")
                log.warning("GitHub sync failed for %s: %s", repo, exc)
            per_repo[repo] = counts

        # Records previously open that vanished from a *complete* repo listing
        # are verified individually: closed, merged, deleted, or still open but
        # filtered out. Unverified records are left untouched until next sync.
        seen = {item.external_id for item in items}
        state_updates: dict[str, str] = {}
        to_verify = [
            (external_id, number)
            for external_id, number in known_open.items()
            if external_id not in seen
            and number is not None
            and external_id.split("#", 1)[0] in completed_repos
        ]
        if rate_limited is None:
            for external_id, number in to_verify[:MAX_VERIFY_PER_SYNC]:
                repo = external_id.split("#", 1)[0]
                try:
                    issue = await self._client.get_issue(repo, number)
                except GitHubRateLimited as exc:
                    rate_limited = exc
                    break
                except GitHubError as exc:
                    errors.append(f"verify {external_id}: {exc}")
                    continue
                state_updates[external_id] = (
                    SourceRecordState.DELETED if issue is None else issue_state(issue)
                )

        summary: dict[str, object] = {
            "repos": per_repo,
            "items": len(items),
            "verified": len(state_updates),
            "unverified": max(0, len(to_verify) - MAX_VERIFY_PER_SYNC),
            "rate_limit_remaining": self._client.rate_limit.remaining,
        }
        if rate_limited is not None:
            summary["rate_limited_until"] = rate_limited.reset_at.isoformat()
        return CollectResult(
            items=items,
            complete=False,
            state_updates=state_updates,
            summary=summary,
            errors=errors,
        )
