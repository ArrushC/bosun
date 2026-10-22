"""Workflow approval steps: approve, reject, cancel, pause, restart and duplicate decisions.

An approval step's request is durable. These tests check that it survives pauses
and restarts, that a decision counts exactly once whether or not the run is
active, what happens to the steps after it and beside it, and that the events the
live graph is built from agree with the database the Steps view reads.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import select, text

from bosun.agents.roles import seed_roles
from bosun.config import Settings
from bosun.db.migrate import upgrade_to_head
from bosun.db.models import ApprovalRequest, Task
from bosun.db.session import Database
from bosun.domain.enums import (
    ApprovalKind,
    ApprovalStatus,
    RunStatus,
    SubtaskStatus,
    TaskStatus,
)
from bosun.orchestration.manager import RunConflict
from bosun.services import Services, build_services
from bosun.workflows.spec import WorkflowSpec, WorkflowStep
from bosun.workflows.validation import validate_workflow

from .conftest import wait_for_run
from .test_workflows import AGENTS, RecordingAdapter, run_workflow, state, step


def approval(key: str, *after: str, **fields: Any) -> WorkflowStep:
    return WorkflowStep(
        key=key,
        kind="approval",
        title=fields.pop("title", f"Approve {key}"),
        instructions=fields.pop("instructions", "Check the work so far."),
        after=list(after),
        **fields,
    )


GATED = [
    step("research", "researcher", title="Research"),
    approval("approve", "research", title="Approve the plan", instructions="Is the cause right?"),
    step("implement", "implementer", "approve", title="Implement", changes_code=True),
    step("docs", "reviewer", "research", title="Check the docs"),
]


@pytest.fixture
async def instance(settings: Settings) -> AsyncIterator[Callable[[], Awaitable[Services]]]:
    """Start Bosun instances on one data folder; the last one started is the live one."""
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    await asyncio.to_thread(upgrade_to_head, settings.resolved_database_url)
    started: list[tuple[Services, Database]] = []

    async def start() -> Services:
        db = Database(settings.resolved_database_url)
        adapter = RecordingAdapter()
        services = build_services(settings, db, {"demo": adapter, "claude": adapter})
        services.adapter = adapter  # type: ignore[attr-defined]
        async with db.session() as session:
            await seed_roles(session)
        started.append((services, db))
        return services

    yield start
    for services, db in started:
        await services.runs.shutdown(timeout=5)
        await db.dispose()


async def until(check: Callable[[], Awaitable[bool]], timeout: float = 20) -> None:
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        if await check():
            return
        await asyncio.sleep(0.02)
    raise AssertionError("timed out")


async def step_approvals(services: Services, run_id: str) -> list[ApprovalRequest]:
    async with services.db.session() as session:
        return list(
            (
                await session.scalars(
                    select(ApprovalRequest)
                    .where(
                        ApprovalRequest.run_id == run_id,
                        ApprovalRequest.kind == ApprovalKind.STEP,
                    )
                    .order_by(ApprovalRequest.created_at)
                )
            ).all()
        )


async def waiting_at(services: Services, run_id: str, key: str) -> ApprovalRequest:
    """Wait until `key` is waiting for a decision and nothing else is still running."""

    async def ready() -> bool:
        _, by_key, _ = await state(services, run_id)
        busy = any(s.status == SubtaskStatus.RUNNING for s in by_key.values())
        return (
            key in by_key
            and by_key[key].status == SubtaskStatus.WAITING_APPROVAL
            and not busy
            and bool([a for a in await step_approvals(services, run_id) if a.status == "pending"])
        )

    await until(ready)
    pending = [a for a in await step_approvals(services, run_id) if a.status == "pending"]
    assert len(pending) == 1
    return pending[0]


async def assert_views_agree(services: Services, run_id: str) -> None:
    """The latest event for every step and approval matches what the database says.

    The live graph is built from events and the Steps view from the database, so
    this is what keeps the two views (and the run's status) consistent.
    """
    events = await services.bus.history(run_id=run_id, limit=10_000)
    steps: dict[str, str] = {}
    approvals: dict[str, str] = {}
    for e in events:
        if e["type"] == "subtask.status":
            steps[e["data"]["subtask_id"]] = e["data"]["to"]
        elif e["type"] == "approval.requested":
            approvals[e["data"]["approval_id"]] = "pending"
        elif e["type"] == "approval.resolved":
            approvals[e["data"]["approval_id"]] = e["status"]
    _, by_key, _ = await state(services, run_id)
    for s in by_key.values():
        assert steps.get(s.id, SubtaskStatus.PENDING) == s.status, (s.key, steps.get(s.id))
    async with services.db.session() as session:
        rows = (
            await session.scalars(select(ApprovalRequest).where(ApprovalRequest.run_id == run_id))
        ).all()
    assert {r.id: r.status for r in rows} == approvals


# -- validation -------------------------------------------------------------------------------
def test_approval_steps_are_checked_and_never_carry_agent_settings() -> None:
    gate = WorkflowStep(
        key="gate", kind="approval", title="Gate", agent="implementer", changes_code=True, retries=3
    )
    assert (gate.agent, gate.changes_code, gate.retries) == (None, False, None)

    def codes(steps: list[WorkflowStep]) -> list[tuple[str, str]]:
        return [(i.level, i.code) for i in validate_workflow(WorkflowSpec(steps=steps), AGENTS)]

    assert codes([approval("a")]) == [("error", "no_agent_steps"), ("warning", "approval_first")]
    assert codes([step("a"), approval("b", "a")]) == []
    assert codes([step("a"), approval("b", "a", instructions="")]) == [
        ("warning", "missing_instructions")
    ]


# -- deciding ---------------------------------------------------------------------------------
async def test_approving_continues_with_the_work_and_the_note(
    instance: Callable[[], Awaitable[Services]], repo: Path
) -> None:
    services = await instance()
    adapter: RecordingAdapter = services.adapter  # type: ignore[attr-defined]
    run_id, task_id = await run_workflow(services, repo, GATED)
    request = await waiting_at(services, run_id, "approve")

    run, by_key, sessions = await state(services, run_id)
    assert run.status == RunStatus.WAITING_APPROVAL
    assert by_key["docs"].status == SubtaskStatus.COMPLETED  # the other branch carried on
    assert by_key["implement"].status == SubtaskStatus.WAITING_DEPENDENCY
    assert not [s for s in sessions if s.subtask_id == by_key["implement"].id]
    assert request.title == "Approve the plan" and request.description == "Is the cause right?"
    assert request.subtask_id == by_key["approve"].id and request.expires_at is None
    checks = request.data["checks"]
    assert [c["title"] for c in checks] == ["Research"]
    assert checks[0]["report"] == by_key["research"].report and checks[0]["agent"] == "Researcher"
    assert request.data["next"] == [{"key": "implement", "title": "Implement"}]
    assert request.data["workflow"] == {"name": "Fix with review", "version": None}

    await services.approvals.resolve(
        request.id, approve=True, message="Keep the change small.", remember=False
    )
    await wait_for_run(services, run_id, timeout=40)
    run, by_key, _ = await state(services, run_id)
    assert run.status == RunStatus.COMPLETED, run.error
    assert by_key["approve"].status == SubtaskStatus.COMPLETED
    assert by_key["approve"].report == "Approved. Note from the approver: Keep the change small."
    # The step after the approval gets the approved work and the note.
    prompt = next(s.prompt for s in adapter.specs if ": Implement\n" in s.prompt)
    assert by_key["research"].report and by_key["research"].report in prompt
    assert "Keep the change small." in prompt
    assert "Approved." not in (run.summary or "")  # the summary is the agents' reports
    async with services.db.session() as session:
        assert (await session.get(Task, task_id)).status == TaskStatus.REVIEW  # type: ignore[union-attr]

    with pytest.raises(ValueError, match="already approved"):
        await services.approvals.resolve(request.id, approve=False, message=None, remember=False)
    await assert_views_agree(services, run_id)


async def test_rejecting_skips_what_follows_but_not_the_other_branches(
    instance: Callable[[], Awaitable[Services]], repo: Path
) -> None:
    services = await instance()
    steps = [
        step("research", "researcher", title="Research"),
        approval("approve", "research", title="Approve the plan"),
        step("implement", "implementer", "approve", title="Implement", changes_code=True),
        step("final", "reviewer", "implement", title="Final check"),
        step("docs", "reviewer", title="Check the docs"),
    ]
    run_id, task_id = await run_workflow(services, repo, steps)
    request = await waiting_at(services, run_id, "approve")
    await services.approvals.resolve(
        request.id, approve=False, message="Wrong cause", remember=False
    )
    await wait_for_run(services, run_id, timeout=40)

    run, by_key, sessions = await state(services, run_id)
    assert {k: s.status for k, s in by_key.items()} == {
        "research": SubtaskStatus.COMPLETED,
        "approve": SubtaskStatus.REJECTED,
        "implement": SubtaskStatus.SKIPPED,
        "final": SubtaskStatus.SKIPPED,
        "docs": SubtaskStatus.COMPLETED,
    }
    assert by_key["approve"].error == "Rejected: Wrong cause"
    assert (
        by_key["implement"].error == "Not run: it waits for Approve the plan, which was rejected."
    )
    assert by_key["final"].error == "Not run: it waits for Approve the plan, which was rejected."
    assert not [s for s in sessions if s.subtask_id in (by_key["implement"].id, by_key["final"].id)]
    # A rejection is a decision, not a failure: the run ends cancelled, the task goes back to Ready.
    assert run.status == RunStatus.CANCELLED
    assert run.status_reason == (
        "Approve the plan was rejected: Wrong cause. Not run: Implement and Final check."
    )
    async with services.db.session() as session:
        assert (await session.get(Task, task_id)).status == TaskStatus.READY  # type: ignore[union-attr]
    await assert_views_agree(services, run_id)


async def test_a_failure_and_a_rejection_fail_the_run_naming_both(
    instance: Callable[[], Awaitable[Services]], repo: Path
) -> None:
    services = await instance()
    adapter: RecordingAdapter = services.adapter  # type: ignore[attr-defined]
    adapter.fail_once = {"Check the API"}
    steps = [
        step("api", "researcher", title="Check the API", retries=0),
        step("research", "researcher", title="Research"),
        approval("approve", "research", title="Approve the plan"),
        step("implement", "implementer", "approve", "api", title="Implement", changes_code=True),
    ]
    run_id, _ = await run_workflow(services, repo, steps)
    request = await waiting_at(services, run_id, "approve")
    _, by_key, _ = await state(services, run_id)
    # Already known not to run, before anyone decides, and why.
    assert by_key["implement"].status == SubtaskStatus.SKIPPED
    assert by_key["implement"].error == "Not run: it waits for Check the API, which failed."
    await services.approvals.resolve(request.id, approve=False, message=None, remember=False)
    await wait_for_run(services, run_id, timeout=40)
    run, by_key, _ = await state(services, run_id)
    assert run.status == RunStatus.FAILED
    assert run.error == (
        "Check the API failed: The agent process exited unexpectedly (simulated); "
        "Approve the plan was rejected. Not run: Implement."
    )
    await assert_views_agree(services, run_id)


async def test_two_decisions_at_once_advance_the_run_once(
    instance: Callable[[], Awaitable[Services]], repo: Path
) -> None:
    services = await instance()
    run_id, _ = await run_workflow(services, repo, GATED)
    request = await waiting_at(services, run_id, "approve")
    results = await asyncio.gather(
        *(
            services.approvals.resolve(request.id, approve=i % 2 == 0, message=None, remember=False)
            for i in range(4)
        ),
        return_exceptions=True,
    )
    winners = [r for r in results if not isinstance(r, BaseException)]
    assert len(winners) == 1, results
    assert all(isinstance(r, ValueError) for r in results if r not in winners), results
    await wait_for_run(services, run_id, timeout=40)
    _, by_key, sessions = await state(services, run_id)
    expected = (
        SubtaskStatus.COMPLETED if winners[0].status == "approved" else SubtaskStatus.REJECTED
    )
    assert by_key["approve"].status == expected
    implement_sessions = [s for s in sessions if s.subtask_id == by_key["implement"].id]
    assert len(implement_sessions) == (1 if expected == SubtaskStatus.COMPLETED else 0)
    assert len(await step_approvals(services, run_id)) == 1
    await assert_views_agree(services, run_id)


# -- pausing, restarting and cancelling ---------------------------------------------------------
async def test_a_pending_approval_survives_a_pause_and_is_not_asked_twice(
    instance: Callable[[], Awaitable[Services]], repo: Path
) -> None:
    services = await instance()
    run_id, _ = await run_workflow(services, repo, GATED)
    request = await waiting_at(services, run_id, "approve")
    await services.runs.pause(run_id)
    await wait_for_run(services, run_id)

    run, by_key, _ = await state(services, run_id)
    assert run.status == RunStatus.PAUSED
    assert by_key["approve"].status == SubtaskStatus.WAITING_APPROVAL
    assert [a.status for a in await step_approvals(services, run_id)] == ["pending"]
    await assert_views_agree(services, run_id)

    # Resuming waits on the same request rather than asking again.
    await services.runs.resume(run_id)
    assert (await waiting_at(services, run_id, "approve")).id == request.id

    async def run_waits() -> bool:
        run, _, _ = await state(services, run_id)
        return run.status == RunStatus.WAITING_APPROVAL

    await until(run_waits)
    await services.approvals.resolve(request.id, approve=True, message=None, remember=False)
    await wait_for_run(services, run_id, timeout=40)
    run, by_key, _ = await state(services, run_id)
    assert run.status == RunStatus.COMPLETED, run.error
    assert len(await step_approvals(services, run_id)) == 1
    assert by_key["approve"].attempts == 1
    await assert_views_agree(services, run_id)


async def test_deciding_while_paused_is_kept_for_when_the_run_resumes(
    instance: Callable[[], Awaitable[Services]], repo: Path
) -> None:
    services = await instance()
    run_id, _ = await run_workflow(services, repo, GATED)
    request = await waiting_at(services, run_id, "approve")
    await services.runs.pause(run_id)
    await wait_for_run(services, run_id)

    await services.approvals.resolve(request.id, approve=True, message="Go", remember=False)
    run, by_key, sessions = await state(services, run_id)
    assert run.status == RunStatus.PAUSED  # deciding doesn't start agents by itself
    assert by_key["approve"].status == SubtaskStatus.COMPLETED
    assert not [s for s in sessions if s.subtask_id == by_key["implement"].id]
    await assert_views_agree(services, run_id)

    await services.runs.resume(run_id)
    await wait_for_run(services, run_id, timeout=40)
    run, by_key, _ = await state(services, run_id)
    assert run.status == RunStatus.COMPLETED, run.error
    assert len(await step_approvals(services, run_id)) == 1


@pytest.mark.parametrize("decide_before_resume", [False, True])
async def test_a_pending_approval_survives_a_restart(
    instance: Callable[[], Awaitable[Services]], repo: Path, decide_before_resume: bool
) -> None:
    first = await instance()
    run_id, _ = await run_workflow(first, repo, GATED)
    request = await waiting_at(first, run_id, "approve")
    # The process dies: no clean shutdown, nothing recorded.
    ctx = first.runs.context(run_id)
    assert ctx is not None and ctx.task is not None
    tasks = [t for t in asyncio.all_tasks() if t.get_name().startswith(("approval:", "subtask:"))]
    for t in [*tasks, ctx.task]:
        t.cancel()
    await asyncio.gather(*tasks, ctx.task, return_exceptions=True)
    async with first.db.session() as session:
        # Whatever the dying process managed to write, startup recovery decides the rest.
        await session.execute(
            text("UPDATE agent_runs SET status = 'waiting_approval' WHERE id = :id"),
            {"id": run_id},
        )
        await session.commit()

    services = await instance()  # the next start-up
    await services.runs.recover()
    run, by_key, _ = await state(services, run_id)
    assert run.status == RunStatus.INTERRUPTED
    assert by_key["approve"].status == SubtaskStatus.WAITING_APPROVAL
    assert [(a.id, a.status) for a in await step_approvals(services, run_id)] == [
        (request.id, ApprovalStatus.PENDING)
    ]
    await assert_views_agree(services, run_id)

    if decide_before_resume:
        await services.approvals.resolve(request.id, approve=True, message=None, remember=False)
        await services.runs.resume(run_id)
    else:
        await services.runs.resume(run_id)
        assert (await waiting_at(services, run_id, "approve")).id == request.id
        await services.approvals.resolve(request.id, approve=True, message=None, remember=False)
    await wait_for_run(services, run_id, timeout=40)
    run, by_key, sessions = await state(services, run_id)
    assert run.status == RunStatus.COMPLETED, run.error
    assert len(await step_approvals(services, run_id)) == 1
    assert len([s for s in sessions if s.subtask_id == by_key["research"].id]) == 1
    await assert_views_agree(services, run_id)


@pytest.mark.parametrize("paused", [False, True])
async def test_cancelling_cancels_the_pending_approval(
    instance: Callable[[], Awaitable[Services]], repo: Path, paused: bool
) -> None:
    services = await instance()
    run_id, task_id = await run_workflow(services, repo, GATED)
    request = await waiting_at(services, run_id, "approve")
    if paused:
        await services.runs.pause(run_id)
        await wait_for_run(services, run_id)
    await services.runs.cancel(run_id)
    await wait_for_run(services, run_id)

    run, by_key, _ = await state(services, run_id)
    assert run.status == RunStatus.CANCELLED
    assert by_key["approve"].status == SubtaskStatus.CANCELLED
    assert [a.status for a in await step_approvals(services, run_id)] == ["cancelled"]
    async with services.db.session() as session:
        assert (await session.get(Task, task_id)).status == TaskStatus.READY  # type: ignore[union-attr]
    with pytest.raises(ValueError, match="already cancelled"):
        await services.approvals.resolve(request.id, approve=True, message=None, remember=False)
    await assert_views_agree(services, run_id)


async def test_approval_steps_cannot_be_skipped_or_retried_while_waiting(
    instance: Callable[[], Awaitable[Services]], repo: Path
) -> None:
    services = await instance()
    run_id, _ = await run_workflow(services, repo, GATED)
    await waiting_at(services, run_id, "approve")
    await services.runs.pause(run_id)
    await wait_for_run(services, run_id)
    _, by_key, _ = await state(services, run_id)
    for action in ("skip", "retry"):
        with pytest.raises(RunConflict, match="waiting for your decision"):
            await services.runs.update_subtask(run_id, by_key["approve"].id, action)


async def test_the_approver_sees_the_changes_so_far(
    instance: Callable[[], Awaitable[Services]], repo: Path
) -> None:
    services = await instance()
    steps = [
        step("implement", "implementer", title="Implement", changes_code=True),
        approval("approve", "implement", title="Approve the change"),
        step("final", "reviewer", "approve", title="Final check"),
    ]
    run_id, _ = await run_workflow(services, repo, steps)
    request = await waiting_at(services, run_id, "approve")
    changes = request.data["changes"]
    assert changes["files"] >= 1 and changes["additions"] >= 1 and changes["paths"]


async def test_a_step_waiting_for_approval_never_runs_twice_under_concurrent_resumes(
    instance: Callable[[], Awaitable[Services]], repo: Path
) -> None:
    services = await instance()
    run_id, _ = await run_workflow(services, repo, GATED)
    await waiting_at(services, run_id, "approve")
    await services.runs.pause(run_id)
    await wait_for_run(services, run_id)
    results = await asyncio.gather(
        *(services.runs.resume(run_id) for _ in range(3)), return_exceptions=True
    )
    assert sum(r is None for r in results) == 1, results
    request = await waiting_at(services, run_id, "approve")
    assert len(await step_approvals(services, run_id)) == 1
    await services.approvals.resolve(request.id, approve=True, message=None, remember=False)
    await wait_for_run(services, run_id, timeout=40)
