"""Regression tests for races and edge cases found in review: duplicate drivers,
stop requests that arrive at awkward moments, approval races, undelivered
messages, unsafe integration and secrets in persisted text."""

from __future__ import annotations

import asyncio
import subprocess
from pathlib import Path

import pytest
from sqlalchemy import select

from bosun.agents.contracts import SessionIO, SessionOutcome, SessionSpec
from bosun.agents.fake import FakeAgentAdapter
from bosun.db.models import AgentRun, AgentSession, ApprovalRequest, Subtask
from bosun.domain.enums import (
    ApprovalStatus,
    ExecutionMode,
    RunStatus,
    SessionStatus,
    SubtaskStatus,
)
from bosun.orchestration.config import RunRequest
from bosun.orchestration.manager import RunConflict
from bosun.orchestration.team import TeamExecutor
from bosun.services import Services

from .conftest import add_task, add_workspace, wait_for_run


async def _run(services: Services, run_id: str) -> AgentRun:
    async with services.db.session() as session:
        run = await session.get(AgentRun, run_id)
        assert run is not None
        return run


async def _sessions(services: Services, run_id: str) -> list[AgentSession]:
    async with services.db.session() as session:
        return list(
            (await session.scalars(select(AgentSession).where(AgentSession.run_id == run_id))).all()
        )


async def _settle(services: Services, run_id: str, timeout: float = 20.0) -> AgentRun:
    """Wait until the run has no driver, including any follow-up it started itself."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        await wait_for_run(services, run_id, timeout=timeout)
        await asyncio.sleep(0.05)
        if services.runs.context(run_id) is None:
            return await _run(services, run_id)
    raise AssertionError("run did not settle")


def _git(path: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(path), "-c", "user.name=t", "-c", "user.email=t@t", *args],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()


async def _wait_until(predicate, timeout: float = 5.0) -> None:  # noqa: ANN001
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        if await predicate():
            return
        await asyncio.sleep(0.02)
    raise AssertionError("condition not reached")


# -- run control races --------------------------------------------------------------


async def test_concurrent_resumes_start_one_driver(services: Services, repo: Path) -> None:
    ws = await add_workspace(services, repo)
    task = await add_task(services, ws)
    services.adapters["demo"].step_delay = 0.1  # type: ignore[attr-defined]
    run = await services.runs.start(task.id, RunRequest(backend="demo"))
    await asyncio.sleep(0.3)
    await services.runs.pause(run.id)
    await wait_for_run(services, run.id)
    assert (await _run(services, run.id)).status == RunStatus.PAUSED

    services.adapters["demo"].step_delay = 0  # type: ignore[attr-defined]
    results = await asyncio.gather(
        services.runs.resume(run.id),
        services.runs.resume(run.id),
        services.runs.retry(run.id),
        return_exceptions=True,
    )
    assert sum(r is None for r in results) == 1, results
    assert all(isinstance(r, RunConflict) for r in results if r is not None)
    final = await _settle(services, run.id)
    assert final.status == RunStatus.COMPLETED
    sessions = await _sessions(services, run.id)
    assert len(sessions) == 1 and sessions[0].turn_count == 2


async def test_cancel_after_pause_wins(services: Services, repo: Path) -> None:
    ws = await add_workspace(services, repo)
    task = await add_task(services, ws)
    services.adapters["demo"].step_delay = 0.1  # type: ignore[attr-defined]
    run = await services.runs.start(task.id, RunRequest(backend="demo"))
    await asyncio.sleep(0.25)
    await services.runs.pause(run.id)
    await services.runs.cancel(run.id)
    await wait_for_run(services, run.id)
    assert (await _run(services, run.id)).status == RunStatus.CANCELLED


async def test_pause_while_waiting_for_a_session_slot(services: Services, repo: Path) -> None:
    await services.limiter.resize(1)
    ws = await add_workspace(services, repo)
    first = await add_task(services, ws, title="First")
    second = await add_task(services, ws, title="Second")
    services.adapters["demo"].step_delay = 0.1  # type: ignore[attr-defined]
    run1 = await services.runs.start(first.id, RunRequest(backend="demo"))

    async def run1_holds_slot() -> bool:
        ctx = services.runs.context(run1.id)
        return bool(ctx and ctx.sessions)

    await _wait_until(run1_holds_slot)
    run2 = await services.runs.start(second.id, RunRequest(backend="demo"))

    async def run2_queued() -> bool:
        ctx = services.runs.context(run2.id)
        return bool(ctx and ctx.sessions)

    await _wait_until(run2_queued)
    await services.runs.pause(run2.id)
    await wait_for_run(services, run2.id)
    await wait_for_run(services, run1.id)

    assert (await _run(services, run2.id)).status == RunStatus.PAUSED
    (queued,) = await _sessions(services, run2.id)
    assert queued.status == SessionStatus.INTERRUPTED and queued.started_at is None
    events = await services.bus.history(run_id=run2.id, limit=1000)
    assert not any(e["type"] == "session.init" for e in events), "agent must not have started"
    assert (await _run(services, run1.id)).status == RunStatus.COMPLETED


# -- approvals ------------------------------------------------------------------------


async def test_simultaneous_approval_decisions_resolve_once(services: Services, repo: Path) -> None:
    ws = await add_workspace(services, repo)
    task = await add_task(services, ws)
    run = await services.runs.start(
        task.id, RunRequest(backend="demo", demo_scenario={"needs_approval": True})
    )

    async def pending() -> ApprovalRequest | None:
        async with services.db.session() as session:
            return await session.scalar(
                select(ApprovalRequest).where(
                    ApprovalRequest.run_id == run.id,
                    ApprovalRequest.status == ApprovalStatus.PENDING,
                )
            )

    await _wait_until(lambda: _is_not_none(pending()))
    approval = await pending()
    assert approval is not None
    results = await asyncio.gather(
        services.approvals.resolve(approval.id, approve=True, message=None, remember=False),
        services.approvals.resolve(approval.id, approve=False, message="no", remember=False),
        return_exceptions=True,
    )
    assert sum(not isinstance(r, BaseException) for r in results) == 1, results
    assert all(isinstance(r, ValueError) for r in results if isinstance(r, BaseException))
    await wait_for_run(services, run.id)
    assert (await _run(services, run.id)).status == RunStatus.COMPLETED


async def _is_not_none(awaitable) -> bool:  # noqa: ANN001
    return (await awaitable) is not None


# -- messages -------------------------------------------------------------------------


class LateMessageAdapter(FakeAgentAdapter):
    """Receives an operator message just after its last turn finished."""

    def __init__(self) -> None:
        super().__init__(step_delay=0)
        self.sent = False

    async def run(self, spec: SessionSpec, io: SessionIO) -> SessionOutcome:
        outcome = await super().run(spec, io)
        if not self.sent:
            self.sent = True
            await io.inbox.put("Also add a changelog entry")
        return outcome


async def test_message_arriving_as_the_agent_finishes_becomes_a_follow_up(
    services: Services, repo: Path
) -> None:
    services.adapters["demo"] = LateMessageAdapter()
    ws = await add_workspace(services, repo)
    task = await add_task(services, ws)
    run = await services.runs.start(task.id, RunRequest(backend="demo"))
    final = await _settle(services, run.id)
    assert final.status == RunStatus.COMPLETED
    events = await services.bus.history(run_id=run.id, limit=10_000)
    follow_ups = [e for e in events if e["type"] == "user.message" and e["data"].get("follow_up")]
    assert follow_ups and "changelog" in follow_ups[0]["data"]["text"]
    assert not any(e["data"].get("undelivered") for e in events)


async def test_interrupting_a_team_with_unfinished_subtasks_is_refused(
    services: Services, repo: Path
) -> None:
    services.adapters["demo"].step_delay = 0.05  # type: ignore[attr-defined]
    ws = await add_workspace(services, repo)
    task = await add_task(services, ws)
    run = await services.runs.start(
        task.id, RunRequest(backend="demo", mode=ExecutionMode.TEAM, require_plan_approval=False)
    )

    async def subtask_running() -> bool:
        async with services.db.session() as session:
            row = await session.scalar(
                select(Subtask).where(
                    Subtask.run_id == run.id, Subtask.status == SubtaskStatus.RUNNING
                )
            )
        ctx = services.runs.context(run.id)
        return row is not None and bool(ctx and ctx.sessions)

    await _wait_until(subtask_running, timeout=10)
    with pytest.raises(RunConflict, match="Interrupting"):
        await services.runs.send_message(run.id, "stop and do X", interrupt=True)
    await services.runs.cancel(run.id)
    await wait_for_run(services, run.id)


# -- scheduler ------------------------------------------------------------------------


async def test_crashing_subtask_runner_fails_the_subtask_instead_of_looping(
    services: Services, repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    original = TeamExecutor._run_subtask
    crashed: list[str] = []

    async def flaky(self: TeamExecutor, subtask_id: str) -> None:
        if not crashed:
            crashed.append(subtask_id)
            raise RuntimeError("scheduler bug")
        await original(self, subtask_id)

    monkeypatch.setattr(TeamExecutor, "_run_subtask", flaky)
    ws = await add_workspace(services, repo)
    task = await add_task(services, ws)
    run = await services.runs.start(
        task.id, RunRequest(backend="demo", mode=ExecutionMode.TEAM, require_plan_approval=False)
    )
    await wait_for_run(services, run.id, timeout=30)
    final = await _run(services, run.id)
    assert final.status == RunStatus.FAILED
    async with services.db.session() as session:
        sub = await session.get(Subtask, crashed[0])
    assert sub is not None and sub.status == SubtaskStatus.FAILED
    assert "scheduler bug" in (sub.error or "")


# -- integration ----------------------------------------------------------------------


async def _completed_run(services: Services, repo: Path) -> AgentRun:
    ws = await add_workspace(services, repo)
    task = await add_task(services, ws)
    run = await services.runs.start(task.id, RunRequest(backend="demo"))
    await wait_for_run(services, run.id)
    row = await _run(services, run.id)
    assert row.status == RunStatus.COMPLETED
    return row


async def test_integrate_refuses_when_nothing_is_ahead(services: Services, repo: Path) -> None:
    run = await _completed_run(services, repo)
    _git(repo, "merge", "-q", "--no-edit", run.branch or "")  # merged by hand
    with pytest.raises(RunConflict, match="no commits"):
        await services.runs.integrate(run.id, "merge")


async def test_integrate_refuses_when_the_base_is_not_on_the_branch(
    services: Services, repo: Path
) -> None:
    run = await _completed_run(services, repo)
    _git(repo, "commit", "-q", "--amend", "-m", "history rewritten")
    with pytest.raises(RunConflict, match="not on 'main'"):
        await services.runs.integrate(run.id, "merge")


async def test_integrate_refuses_runs_started_on_a_detached_head(
    services: Services, repo: Path
) -> None:
    _git(repo, "checkout", "-q", "--detach")
    run = await _completed_run(services, repo)
    assert run.base_branch is None
    with pytest.raises(RunConflict, match="detached"):
        await services.runs.integrate(run.id, "merge")


async def test_integrate_includes_changes_left_uncommitted(services: Services, repo: Path) -> None:
    run = await _completed_run(services, repo)
    (Path(run.worktree_path or "") / "late.txt").write_text("written after the last commit\n")
    await services.runs.integrate(run.id, "merge")
    assert (repo / "late.txt").read_text() == "written after the last commit\n"


# -- redaction ------------------------------------------------------------------------

SECRET = "ghp_" + "a1B2c3D4e5" * 4


class LeakyAdapter(FakeAgentAdapter):
    async def run(self, spec: SessionSpec, io: SessionIO) -> SessionOutcome:
        outcome = await super().run(spec, io)
        outcome.result = (
            f"Deployed. Used GITHUB_TOKEN={SECRET} and https://bot:hunter22@example.com"
        )
        return outcome


async def test_secrets_never_reach_persisted_results(services: Services, repo: Path) -> None:
    services.adapters["demo"] = LeakyAdapter(step_delay=0)
    run = await _completed_run(services, repo)
    (session,) = await _sessions(services, run.id)
    stored = " ".join(filter(None, [session.result, run.summary]))
    assert SECRET not in stored and "hunter22" not in stored
    assert "[REDACTED]" in (session.result or "")
    events = await services.bus.history(run_id=run.id, limit=10_000)
    assert SECRET not in str(events)
