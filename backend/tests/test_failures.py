"""Failure handling: auth, crashes, permissions, timeouts, missing workspaces and restart recovery."""

from __future__ import annotations

import asyncio
import shutil
from pathlib import Path

import pytest
from sqlalchemy import select

from bosun.agents.contracts import ErrorKind, SessionIO, SessionOutcome, SessionSpec
from bosun.agents.fake import FakeAgentAdapter
from bosun.agents.providers import ProviderStatus
from bosun.db.models import AgentRun, AgentSession, ApprovalRequest, Subtask, Task
from bosun.domain.enums import (
    ApprovalStatus,
    ExecutionMode,
    Provider,
    RunStatus,
    SessionStatus,
    SubtaskStatus,
    TaskStatus,
)
from bosun.orchestration.config import RunRequest
from bosun.orchestration.manager import RunConflict
from bosun.services import Services

from .conftest import add_task, add_workspace, wait_for_run


class CountingAdapter(FakeAgentAdapter):
    def __init__(self, fail_first: int = 0, error_kind: str = ErrorKind.PROCESS_CRASH) -> None:
        super().__init__(step_delay=0)
        self.calls = 0
        self.fail_first = fail_first
        self.error_kind = error_kind

    async def run(self, spec: SessionSpec, io: SessionIO) -> SessionOutcome:
        self.calls += 1
        if self.calls <= self.fail_first:
            return SessionOutcome(
                status=SessionStatus.FAILED, error="boom", error_kind=self.error_kind
            )
        return await super().run(spec, io)


async def _run(services: Services, run_id: str) -> AgentRun:
    async with services.db.session() as session:
        run = await session.get(AgentRun, run_id)
        assert run is not None
        return run


async def _wait_for_approval(services: Services, run_id: str) -> str:
    for _ in range(300):
        async with services.db.session() as session:
            row = await session.scalar(
                select(ApprovalRequest).where(
                    ApprovalRequest.run_id == run_id,
                    ApprovalRequest.status == ApprovalStatus.PENDING,
                )
            )
        if row is not None:
            return row.id
        await asyncio.sleep(0.02)
    raise AssertionError("no approval requested")


async def test_missing_authentication_fails_without_fallback(
    services: Services, repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    adapter = CountingAdapter()
    services.adapters["claude"] = adapter

    async def not_ready(prefs, cfg, force=False):  # noqa: ANN001, ARG001
        return ProviderStatus(
            Provider.SUBSCRIPTION, False, "Not signed in to a Claude subscription", {}, 0.0
        )

    monkeypatch.setattr("bosun.orchestration.manager.check_provider_status", not_ready)
    ws = await add_workspace(services, repo)
    task = await add_task(services, ws)
    run = await services.runs.start(task.id, RunRequest(backend="claude"))
    await wait_for_run(services, run.id)
    row = await _run(services, run.id)
    assert row.status == RunStatus.FAILED
    assert row.error_kind == ErrorKind.AUTH_REQUIRED
    assert "Not signed in" in (row.error or "")
    assert adapter.calls == 0  # never fell back to another provider or ran the agent
    async with services.db.session() as session:
        assert (await session.get(Task, task.id)).status == TaskStatus.BLOCKED  # type: ignore[union-attr]


async def test_failed_agent_is_reported_and_task_blocked(services: Services, repo: Path) -> None:
    services.adapters["demo"] = CountingAdapter(fail_first=1)
    ws = await add_workspace(services, repo)
    task = await add_task(services, ws)
    run = await services.runs.start(task.id, RunRequest(backend="demo"))
    await wait_for_run(services, run.id)
    row = await _run(services, run.id)
    assert row.status == RunStatus.FAILED and row.error == "boom"
    events = await services.bus.history(run_id=run.id)
    assert any(e["type"] == "session.failed" for e in events)
    assert any(e["type"] == "run.failed" for e in events)

    # Explicit retry succeeds; history is preserved.
    await services.runs.retry(run.id)
    await wait_for_run(services, run.id)
    assert (await _run(services, run.id)).status == RunStatus.COMPLETED


async def test_team_subtask_retries_transient_failures(services: Services, repo: Path) -> None:
    # Coordinator succeeds (call 1); the first subtask attempt crashes (call 2), the retry succeeds.
    adapter = CountingAdapter()
    original = adapter.run

    async def flaky(spec: SessionSpec, io: SessionIO) -> SessionOutcome:
        if spec.role == "researcher" and not getattr(adapter, "_failed_once", False):
            adapter._failed_once = True  # type: ignore[attr-defined]
            return SessionOutcome(
                status=SessionStatus.FAILED, error="crashed", error_kind=ErrorKind.PROCESS_CRASH
            )
        return await original(spec, io)

    adapter.run = flaky  # type: ignore[method-assign]
    services.adapters["demo"] = adapter
    ws = await add_workspace(services, repo)
    task = await add_task(services, ws)
    run = await services.runs.start(
        task.id, RunRequest(backend="demo", mode=ExecutionMode.TEAM, require_plan_approval=False)
    )
    await wait_for_run(services, run.id, timeout=30)
    assert (await _run(services, run.id)).status == RunStatus.COMPLETED
    async with services.db.session() as session:
        s1 = await session.scalar(
            select(Subtask).where(Subtask.run_id == run.id, Subtask.key == "s1")
        )
    assert s1 is not None and s1.attempts == 2
    events = await services.bus.history(run_id=run.id)
    assert any(e["type"] == "subtask.retry" for e in events)


async def test_non_retryable_failure_stops_dependants(services: Services, repo: Path) -> None:
    adapter = CountingAdapter()
    original = adapter.run

    async def limited(spec: SessionSpec, io: SessionIO) -> SessionOutcome:
        if spec.role == "researcher":
            return SessionOutcome(
                status=SessionStatus.FAILED,
                error="Claude usage limit reached",
                error_kind=ErrorKind.USAGE_LIMIT,
            )
        return await original(spec, io)

    adapter.run = limited  # type: ignore[method-assign]
    services.adapters["demo"] = adapter
    ws = await add_workspace(services, repo)
    task = await add_task(services, ws)
    run = await services.runs.start(
        task.id, RunRequest(backend="demo", mode=ExecutionMode.TEAM, require_plan_approval=False)
    )
    await wait_for_run(services, run.id, timeout=30)
    row = await _run(services, run.id)
    assert row.status == RunStatus.FAILED
    async with services.db.session() as session:
        subs = {
            s.key: s
            for s in (await session.scalars(select(Subtask).where(Subtask.run_id == run.id))).all()
        }
    assert (
        subs["s1"].status == SubtaskStatus.FAILED and subs["s1"].attempts == 1
    )  # usage limits are not retried
    assert subs["s2"].status == SubtaskStatus.WAITING_DEPENDENCY  # dependants never started

    # The operator can skip the failed subtask and resume the remaining plan.
    adapter.run = original  # type: ignore[method-assign]
    await services.runs.update_subtask(run.id, subs["s1"].id, "skip")
    await wait_for_run(services, run.id, timeout=30)
    assert (await _run(services, run.id)).status == RunStatus.COMPLETED


async def test_denied_permission_is_reported_and_agent_continues(
    services: Services, repo: Path
) -> None:
    ws = await add_workspace(services, repo)
    task = await add_task(services, ws)
    run = await services.runs.start(
        task.id, RunRequest(backend="demo", demo_scenario={"needs_approval": True})
    )
    approval_id = await _wait_for_approval(services, run.id)
    assert (await _run(services, run.id)).status == RunStatus.WAITING_APPROVAL
    await services.approvals.resolve(
        approval_id, approve=False, message="No migrations today", remember=False
    )
    await wait_for_run(services, run.id)
    assert (await _run(services, run.id)).status == RunStatus.COMPLETED
    events = await services.bus.history(run_id=run.id)
    denied = [e for e in events if e["type"] == "tool.result" and e["data"].get("is_error")]
    assert denied and "No migrations today" in denied[0]["data"]["output"]
    resolved = [e for e in events if e["type"] == "approval.resolved"]
    assert resolved[0]["status"] == ApprovalStatus.DENIED
    # A second resolution is rejected.
    with pytest.raises(ValueError):
        await services.approvals.resolve(approval_id, approve=True, message=None, remember=False)


async def test_approval_timeout_denies_and_expires(
    services: Services, repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def short_timeout() -> float:
        return 0.2

    monkeypatch.setattr(services.approvals, "_settings_timeout", short_timeout)
    ws = await add_workspace(services, repo)
    task = await add_task(services, ws)
    run = await services.runs.start(
        task.id, RunRequest(backend="demo", demo_scenario={"needs_approval": True})
    )
    await wait_for_run(services, run.id)
    assert (await _run(services, run.id)).status == RunStatus.COMPLETED
    async with services.db.session() as session:
        approval = await session.scalar(
            select(ApprovalRequest).where(ApprovalRequest.run_id == run.id)
        )
    assert approval is not None and approval.status == ApprovalStatus.EXPIRED


async def test_remembered_approval_is_reused(services: Services, repo: Path) -> None:
    ws = await add_workspace(services, repo)
    task = await add_task(services, ws)
    run = await services.runs.start(
        task.id, RunRequest(backend="demo", demo_scenario={"needs_approval": True})
    )
    approval_id = await _wait_for_approval(services, run.id)
    await services.approvals.resolve(approval_id, approve=True, message=None, remember=True)
    await wait_for_run(services, run.id)
    # Resume via retry-style follow-up: the same command is auto-approved for this run.
    run_row = await _run(services, run.id)
    assert run_row.config["remembered_approvals"] == [
        {"tool": "Bash", "command": "./scripts/migrate.sh --dry-run"}
    ]


async def test_missing_workspace_is_rejected(services: Services, repo: Path) -> None:
    ws = await add_workspace(services, repo)
    task = await add_task(services, ws)
    shutil.rmtree(repo)
    with pytest.raises(RunConflict, match="no longer exists"):
        await services.runs.start(task.id, RunRequest(backend="demo"))


async def test_task_without_workspace_is_rejected(services: Services) -> None:
    task = await add_task(services, None)
    with pytest.raises(RunConflict, match="workspace"):
        await services.runs.start(task.id, RunRequest(backend="demo"))


async def test_restart_recovery_marks_stale_runs_interrupted_and_resumes(
    services: Services, repo: Path
) -> None:
    ws = await add_workspace(services, repo)
    task = await add_task(services, ws)
    run = await services.runs.start(task.id, RunRequest(backend="demo"))
    await wait_for_run(services, run.id)

    # Simulate a crash mid-run: the database still says "running", nothing is alive.
    async with services.db.session() as session:
        row = await session.get(AgentRun, run.id)
        row.status = RunStatus.RUNNING  # type: ignore[union-attr]
        ses = await session.scalar(select(AgentSession).where(AgentSession.run_id == run.id))
        ses.status = SessionStatus.RUNNING  # type: ignore[union-attr]
        ses.pid = 999_999  # type: ignore[union-attr]
        session.add(
            ApprovalRequest(run_id=run.id, task_id=task.id, kind="tool_permission", title="stale")
        )
        await session.commit()

    counts = await services.runs.recover()
    assert counts["runs"] == 1 and counts["sessions"] == 1
    row = await _run(services, run.id)
    assert row.status == RunStatus.INTERRUPTED
    assert "restarted" in (row.status_reason or "")
    async with services.db.session() as session:
        ses = await session.scalar(select(AgentSession).where(AgentSession.run_id == run.id))
        approval = await session.scalar(
            select(ApprovalRequest).where(ApprovalRequest.run_id == run.id)
        )
    assert ses is not None and ses.status == SessionStatus.INTERRUPTED and ses.pid is None
    assert approval is not None and approval.status == ApprovalStatus.EXPIRED

    await services.runs.resume(run.id)
    await wait_for_run(services, run.id)
    assert (await _run(services, run.id)).status == RunStatus.COMPLETED


async def test_resume_fails_cleanly_when_worktree_was_deleted(
    services: Services, repo: Path
) -> None:
    ws = await add_workspace(services, repo)
    task = await add_task(services, ws)
    run = await services.runs.start(task.id, RunRequest(backend="demo"))
    await wait_for_run(services, run.id)
    row = await _run(services, run.id)
    async with services.db.session() as session:
        stale = await session.get(AgentRun, run.id)
        stale.status = RunStatus.INTERRUPTED  # type: ignore[union-attr]
        await session.commit()
    shutil.rmtree(
        Path(row.worktree_path or "").parent
        if Path(row.worktree_path or "").name != row.id
        else row.worktree_path or ""
    )
    await services.runs.resume(run.id)
    await wait_for_run(services, run.id)
    failed = await _run(services, run.id)
    assert failed.status == RunStatus.FAILED and failed.error_kind == ErrorKind.WORKSPACE
