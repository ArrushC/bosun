"""Restart recovery for a team subtask whose agent finished just before Bosun stopped.

The process is "killed" at three points after the implementer's session has
completed: before its changes are committed, between the commit and the merge
into the run branch, and after the merge but before the subtask is recorded as
done. Startup recovery plus a resume must record the subtask once, without
running it again or applying its changes twice.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import select

from bosun.db.models import AgentRun, AgentSession, Subtask
from bosun.domain.enums import ExecutionMode, RunStatus, SubtaskStatus
from bosun.orchestration.config import RunRequest
from bosun.services import Services

from .conftest import add_task, add_workspace, wait_for_run

TEAM = RunRequest(backend="demo", mode=ExecutionMode.TEAM, require_plan_approval=False)
IMPLEMENTER_LINE = "Demo change made by the implementer agent."


async def _hang() -> None:
    await asyncio.Event().wait()


def _stop_once(services: Services, point: str, reached: asyncio.Event) -> Callable[[], None]:
    """Make the implementer subtask (s2) hang at `point`; returns an undo function."""
    worktrees = services.worktrees
    commit_all, merge_into = worktrees.commit_all, worktrees.merge_into

    async def commit_hook(path: Path, message: str) -> str | None:
        if point == "before_commit" and "__s2" in str(path) and not reached.is_set():
            reached.set()
            await _hang()
        return await commit_all(path, message)

    async def merge_hook(target: Path, branch: str, message: str) -> Any:
        is_step = branch.endswith("--s2") and not reached.is_set()
        if point == "before_merge" and is_step:
            reached.set()
            await _hang()
        result = await merge_into(target, branch, message)
        if point == "after_merge" and is_step:
            reached.set()
            await _hang()
        return result

    worktrees.commit_all = commit_hook  # type: ignore[method-assign]
    worktrees.merge_into = merge_hook  # type: ignore[method-assign]

    def undo() -> None:
        worktrees.commit_all = commit_all  # type: ignore[method-assign]
        worktrees.merge_into = merge_into  # type: ignore[method-assign]

    return undo


async def _kill(services: Services, run_id: str) -> None:
    """Stop the run as a dying process would: no cleanup, no status updates."""
    ctx = services.runs.context(run_id)
    assert ctx is not None and ctx.task is not None
    steps = [t for t in asyncio.all_tasks() if t.get_name().startswith("subtask:")]
    for t in steps:
        t.cancel()
    ctx.task.cancel()
    await asyncio.gather(*steps, ctx.task, return_exceptions=True)


async def _state(services: Services, run_id: str) -> tuple[AgentRun, Subtask, list[AgentSession]]:
    async with services.db.session() as session:
        run = await session.get(AgentRun, run_id)
        assert run is not None
        step = await session.scalar(
            select(Subtask).where(Subtask.run_id == run_id, Subtask.key == "s2")
        )
        assert step is not None
        sessions = list(
            (
                await session.scalars(
                    select(AgentSession).where(AgentSession.subtask_id == step.id)
                )
            ).all()
        )
    return run, step, sessions


@pytest.mark.parametrize("point", ["before_commit", "before_merge", "after_merge"])
async def test_finished_subtask_is_recorded_once_after_a_crash(
    services: Services, repo: Path, point: str
) -> None:
    reached = asyncio.Event()
    undo: Callable[[], None] | None = _stop_once(services, point, reached)
    ws = await add_workspace(services, repo)
    task = await add_task(services, ws, title="Recover merged step")
    run = await services.runs.start(task.id, TEAM)
    await asyncio.wait_for(reached.wait(), 20)
    await _kill(services, run.id)

    await services.runs.recover()  # what the next start-up does
    row, step, sessions = await _state(services, run.id)
    assert row.status == RunStatus.INTERRUPTED
    assert step.status == SubtaskStatus.PENDING
    assert [s.status for s in sessions] == ["completed"]

    assert undo is not None
    undo()
    await services.runs.resume(run.id)
    await wait_for_run(services, run.id, timeout=40)

    row, step, sessions = await _state(services, run.id)
    assert row.status == RunStatus.COMPLETED, row.error
    assert step.status == SubtaskStatus.COMPLETED and step.report
    assert len(sessions) == 1, "the implementer ran again"
    notes = Path(row.worktree_path or "") / "bosun-demo" / "recover-merged-step.md"
    assert notes.read_text().count(IMPLEMENTER_LINE) == 1, "its change was applied twice"
    events = await services.bus.history(run_id=run.id, limit=10_000)
    assert any(e["type"] == "subtask.recovered" and e["data"]["key"] == "s2" for e in events)


async def test_operator_retry_still_reruns_a_subtask(services: Services, repo: Path) -> None:
    """Recovery must not swallow deliberate retries: they reset attempts and start fresh."""
    ws = await add_workspace(services, repo)
    task = await add_task(services, ws, title="Retry a subtask")
    run = await services.runs.start(task.id, TEAM)
    await wait_for_run(services, run.id, timeout=40)
    async with services.db.session() as session:
        run_row = await session.get(AgentRun, run.id)
        step = await session.scalar(
            select(Subtask).where(Subtask.run_id == run.id, Subtask.key == "s4")
        )
        assert run_row is not None and step is not None
        # Leave s4 as a failed step whose session had completed, as a merge conflict does.
        step.status, run_row.status = SubtaskStatus.CONFLICT, RunStatus.FAILED
        await session.commit()
        step_id = step.id
    await services.runs.update_subtask(run.id, step_id, "retry")
    await wait_for_run(services, run.id, timeout=40)
    async with services.db.session() as session:
        sessions = (
            await session.scalars(select(AgentSession).where(AgentSession.subtask_id == step_id))
        ).all()
    assert len(sessions) == 2
