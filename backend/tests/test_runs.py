"""Session lifecycle, orchestration, worktrees and integration with the deterministic adapter."""

from __future__ import annotations

import asyncio
import subprocess
from pathlib import Path

import pytest
from sqlalchemy import select

from bosun.db.models import AgentRun, AgentSession, Subtask, Task
from bosun.domain.enums import ExecutionMode, RunStatus, SubtaskStatus, TaskStatus
from bosun.orchestration.config import RunRequest
from bosun.orchestration.manager import RunConflict
from bosun.services import Services

from .conftest import add_task, add_workspace, wait_for_run


async def _run(services: Services, run_id: str) -> AgentRun:
    async with services.db.session() as session:
        run = await session.get(AgentRun, run_id)
        assert run is not None
        return run


async def _task(services: Services, task_id: str) -> Task:
    async with services.db.session() as session:
        task = await session.get(Task, task_id)
        assert task is not None
        return task


async def _events(services: Services, run_id: str) -> list[dict]:
    return await services.bus.history(run_id=run_id, limit=10_000)


def _git(path: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(path), *args], capture_output=True, text=True, check=True
    ).stdout.strip()


async def test_single_run_completes_in_isolated_worktree(services: Services, repo: Path) -> None:
    ws = await add_workspace(services, repo)
    task = await add_task(services, ws, title="Add notes", status=TaskStatus.READY)

    run = await services.runs.start(task.id, RunRequest(backend="demo", mode=ExecutionMode.SINGLE))
    await wait_for_run(services, run.id)

    run = await _run(services, run.id)
    assert run.status == RunStatus.COMPLETED, run.error
    assert run.isolation == "worktree" and run.branch and run.branch.startswith("bosun/bos-1")
    # Agent success means "ready for review", never "done".
    assert (await _task(services, task.id)).status == TaskStatus.REVIEW
    # The user's checkout is untouched; the agent's change lives on the run branch.
    assert not (repo / "bosun-demo").exists()
    assert "bosun-demo/" in _git(repo, "show", "--stat", "--format=", run.branch)
    diff = await services.runs.diff(run.id)
    assert diff["available"] and diff["files"] and diff["additions"] > 0

    events = await _events(services, run.id)
    seqs = [e["seq"] for e in events]
    assert seqs == sorted(seqs)
    types = [e["type"] for e in events]
    for expected in (
        "run.created",
        "workspace.prepared",
        "session.created",
        "session.init",
        "tool.call",
        "tool.result",
        "artifact.file",
        "session.completed",
        "run.completed",
    ):
        assert expected in types, expected
    # Tool calls and results pair up by tool_use_id.
    calls = {e["data"]["tool_use_id"] for e in events if e["type"] == "tool.call"}
    results = {e["data"]["tool_use_id"] for e in events if e["type"] == "tool.result"}
    assert calls == results


async def test_integrate_merge_and_discard(services: Services, repo: Path) -> None:
    ws = await add_workspace(services, repo)
    task = await add_task(services, ws)
    run = await services.runs.start(task.id, RunRequest(backend="demo"))
    await wait_for_run(services, run.id)
    result = await services.runs.integrate(run.id, "merge")
    assert result["commit"]
    assert (repo / "bosun-demo").is_dir()
    with pytest.raises(RunConflict):
        await services.runs.integrate(run.id, "merge")

    task2 = await add_task(services, ws, title="Second")
    run2 = await services.runs.start(task2.id, RunRequest(backend="demo"))
    await wait_for_run(services, run2.id)
    run2_row = await _run(services, run2.id)
    await services.runs.integrate(run2.id, "discard")
    assert not Path(run2_row.worktree_path or "").exists()
    assert run2_row.branch not in _git(repo, "branch", "--list")


async def test_integration_refuses_dirty_checkout(services: Services, repo: Path) -> None:
    ws = await add_workspace(services, repo)
    task = await add_task(services, ws)
    run = await services.runs.start(task.id, RunRequest(backend="demo"))
    await wait_for_run(services, run.id)
    (repo / "README.md").write_text("local edit\n")
    with pytest.raises(RunConflict, match="uncommitted"):
        await services.runs.integrate(run.id, "merge")


async def test_non_git_workspace_runs_in_place(services: Services, tmp_path: Path) -> None:
    plain = tmp_path / "plain"
    plain.mkdir()
    (plain / "README.md").write_text("# plain\n")
    ws = await add_workspace(services, plain)
    task = await add_task(services, ws)
    run = await services.runs.start(task.id, RunRequest(backend="demo"))
    await wait_for_run(services, run.id)
    run = await _run(services, run.id)
    assert run.status == RunStatus.COMPLETED
    assert run.isolation == "in_place"
    assert (plain / "bosun-demo").is_dir()


async def test_team_run_respects_dependencies_and_routes_reports(
    services: Services, repo: Path
) -> None:
    ws = await add_workspace(services, repo)
    task = await add_task(services, ws, title="History endpoint")
    run = await services.runs.start(
        task.id, RunRequest(backend="demo", mode=ExecutionMode.TEAM, require_plan_approval=False)
    )
    await wait_for_run(services, run.id, timeout=40)
    run_row = await _run(services, run.id)
    assert run_row.status == RunStatus.COMPLETED, run_row.error
    assert run_row.strategy == "team"
    assert run_row.summary and "Outcome" in run_row.summary

    async with services.db.session() as session:
        subtasks = (
            await session.scalars(
                select(Subtask).where(Subtask.run_id == run.id).order_by(Subtask.position)
            )
        ).all()
        sessions = (
            await session.scalars(select(AgentSession).where(AgentSession.run_id == run.id))
        ).all()
    assert [s.key for s in subtasks] == ["s1", "s2", "s3", "s4"]
    assert all(s.status == SubtaskStatus.COMPLETED for s in subtasks)
    coordinator = [s for s in sessions if s.kind == "coordinator"]
    assert len(coordinator) == 1 and coordinator[0].turn_count == 2  # planning + aggregation
    workers = [s for s in sessions if s.kind == "agent"]
    assert {s.parent_session_id for s in workers} == {coordinator[0].id}

    events = await _events(services, run.id)
    started = {}
    finished = {}
    for e in events:
        if e["type"] == "subtask.status" and e["data"]["to"] == "running":
            started.setdefault(e["data"]["key"], e["seq"])
        if e["type"] == "subtask.status" and e["data"]["to"] == "completed":
            finished[e["data"]["key"]] = e["seq"]
    # s2 depends on s1; s3 and s4 depend on s2.
    assert finished["s1"] < started["s2"]
    assert finished["s2"] < started["s3"] and finished["s2"] < started["s4"]

    messages = [e for e in events if e["type"] == "agent.message"]
    kinds = {(m["data"]["kind"]) for m in messages}
    assert {"assignment", "report"} <= kinds
    assert any(m["data"]["to_session_id"] == coordinator[0].id for m in messages)
    assert all(m["data"]["routed_by"] == "bosun" for m in messages)

    # Write-capable subtasks were merged into the run branch.
    log = _git(repo, "log", "--format=%s", run_row.branch or "")
    assert "Merge BOS-1 s2" in log and "Merge BOS-1 s3" in log


async def test_plan_approval_checkpoint(services: Services, repo: Path) -> None:
    ws = await add_workspace(services, repo)
    task = await add_task(services, ws)
    run = await services.runs.start(
        task.id, RunRequest(backend="demo", mode=ExecutionMode.TEAM, require_plan_approval=True)
    )
    approval_id = None
    for _ in range(200):
        events = await _events(services, run.id)
        pending = [
            e for e in events if e["type"] == "approval.requested" and e["data"]["kind"] == "plan"
        ]
        if pending:
            approval_id = pending[0]["data"]["approval_id"]
            break
        await asyncio.sleep(0.02)
    assert approval_id
    assert (await _run(services, run.id)).status == RunStatus.WAITING_APPROVAL
    async with services.db.session() as session:
        assert not (
            await session.scalars(
                select(AgentSession).where(
                    AgentSession.run_id == run.id, AgentSession.kind == "agent"
                )
            )
        ).all()
    await services.approvals.resolve(
        approval_id, approve=False, message="Too broad", remember=False
    )
    await wait_for_run(services, run.id)
    run_row = await _run(services, run.id)
    assert run_row.status == RunStatus.CANCELLED
    assert (await _task(services, task.id)).status == TaskStatus.READY


async def test_auto_mode_uses_single_agent_for_small_plans(services: Services, repo: Path) -> None:
    ws = await add_workspace(services, repo)
    task = await add_task(services, ws)
    run = await services.runs.start(
        task.id,
        RunRequest(backend="demo", mode=ExecutionMode.AUTO, demo_scenario={"strategy": "single"}),
    )
    await wait_for_run(services, run.id)
    run_row = await _run(services, run.id)
    assert run_row.status == RunStatus.COMPLETED
    assert run_row.strategy == "single"
    async with services.db.session() as session:
        sessions = (
            await session.scalars(select(AgentSession).where(AgentSession.run_id == run.id))
        ).all()
    # Coordinator plans once; no aggregation turn is spent for a single subtask.
    assert len(sessions) == 2


async def test_duplicate_execution_is_prevented(services: Services, repo: Path) -> None:
    ws = await add_workspace(services, repo)
    task = await add_task(services, ws)
    services.adapters["demo"].step_delay = 0.05  # type: ignore[attr-defined]
    results = await asyncio.gather(
        *[services.runs.start(task.id, RunRequest(backend="demo")) for _ in range(5)],
        return_exceptions=True,
    )
    runs = [r for r in results if isinstance(r, AgentRun)]
    conflicts = [r for r in results if isinstance(r, RunConflict)]
    assert len(runs) == 1 and len(conflicts) == 4
    await services.runs.cancel(runs[0].id)
    await wait_for_run(services, runs[0].id)


async def test_idempotency_key_returns_same_run(services: Services, repo: Path) -> None:
    ws = await add_workspace(services, repo)
    task = await add_task(services, ws)
    first = await services.runs.start(
        task.id, RunRequest(backend="demo"), idempotency_key="click-1"
    )
    second = await services.runs.start(
        task.id, RunRequest(backend="demo"), idempotency_key="click-1"
    )
    assert first.id == second.id
    await wait_for_run(services, first.id)


async def test_cancel_running_session(services: Services, repo: Path) -> None:
    ws = await add_workspace(services, repo)
    task = await add_task(services, ws)
    services.adapters["demo"].step_delay = 0.1  # type: ignore[attr-defined]
    run = await services.runs.start(task.id, RunRequest(backend="demo"))
    await asyncio.sleep(0.25)
    await services.runs.cancel(run.id)
    await wait_for_run(services, run.id)
    run_row = await _run(services, run.id)
    assert run_row.status == RunStatus.CANCELLED
    async with services.db.session() as session:
        statuses = {
            s.status
            for s in (
                await session.scalars(select(AgentSession).where(AgentSession.run_id == run.id))
            ).all()
        }
    assert statuses <= {"interrupted", "cancelled"}
    assert services.runs.context(run.id) is None


async def test_pause_and_resume_continues_the_same_session(services: Services, repo: Path) -> None:
    ws = await add_workspace(services, repo)
    task = await add_task(services, ws)
    services.adapters["demo"].step_delay = 0.1  # type: ignore[attr-defined]
    run = await services.runs.start(task.id, RunRequest(backend="demo"))
    await asyncio.sleep(0.3)
    await services.runs.pause(run.id)
    await wait_for_run(services, run.id)
    assert (await _run(services, run.id)).status == RunStatus.PAUSED
    async with services.db.session() as session:
        first = (
            await session.scalars(select(AgentSession).where(AgentSession.run_id == run.id))
        ).one()
    assert first.status == "interrupted" and first.sdk_session_id

    services.adapters["demo"].step_delay = 0  # type: ignore[attr-defined]
    await services.runs.resume(run.id)
    await wait_for_run(services, run.id)
    assert (await _run(services, run.id)).status == RunStatus.COMPLETED
    async with services.db.session() as session:
        again = (
            await session.scalars(select(AgentSession).where(AgentSession.run_id == run.id))
        ).all()
    assert (
        len(again) == 1
        and again[0].turn_count == 2
        and again[0].sdk_session_id == first.sdk_session_id
    )


async def test_follow_up_message_reopens_completed_run(services: Services, repo: Path) -> None:
    ws = await add_workspace(services, repo)
    task = await add_task(services, ws)
    run = await services.runs.start(task.id, RunRequest(backend="demo"))
    await wait_for_run(services, run.id)
    delivery = await services.runs.send_message(run.id, "Also update the README")
    assert delivery == "started"
    await wait_for_run(services, run.id)
    run_row = await _run(services, run.id)
    assert run_row.status == RunStatus.COMPLETED
    assert "Follow-up" in (run_row.summary or "")
    events = await _events(services, run.id)
    assert any(e["type"] == "user.message" and "README" in e["data"]["text"] for e in events)
    assert "follow-up.md" in _git(repo, "show", "--stat", "--format=", run_row.branch or "")


async def test_message_queued_to_live_session(services: Services, repo: Path) -> None:
    ws = await add_workspace(services, repo)
    task = await add_task(services, ws)
    services.adapters["demo"].step_delay = 0.05  # type: ignore[attr-defined]
    run = await services.runs.start(task.id, RunRequest(backend="demo"))
    for _ in range(100):
        ctx = services.runs.context(run.id)
        if ctx and ctx.sessions:
            break
        await asyncio.sleep(0.01)
    assert await services.runs.send_message(run.id, "Prefer small commits") == "queued"
    await wait_for_run(services, run.id)
    events = await _events(services, run.id)
    delivered = [e for e in events if e["type"] == "user.message" and e["data"].get("delivered")]
    assert delivered and delivered[0]["data"]["text"] == "Prefer small commits"


async def test_agent_commits_skip_cache_files(services: Services, tmp_path: Path) -> None:
    from bosun.orchestration.git import WorktreeManager

    repo = tmp_path / "r"
    from .conftest import make_repo

    make_repo(repo, {"a.py": "x = 1\n"})
    (repo / "a.py").write_text("x = 2\n")
    (repo / "__pycache__").mkdir()
    (repo / "__pycache__" / "a.cpython-312.pyc").write_bytes(b"\0junk")
    (repo / "pkg" / ".pytest_cache").mkdir(parents=True)
    (repo / "pkg" / ".pytest_cache" / "v").write_text("cache")
    sha = await WorktreeManager(tmp_path / "wt").commit_all(repo, "agent change")
    assert sha
    files = _git(repo, "show", "--name-only", "--format=", "HEAD").split()
    assert files == ["a.py"]
    # Nothing to commit when only caches changed.
    (repo / "__pycache__" / "b.pyc").write_bytes(b"\0")
    assert await WorktreeManager(tmp_path / "wt").commit_all(repo, "noop") is None


async def test_pull_request_tasks_start_from_the_pr_head(
    services: Services, tmp_path: Path
) -> None:
    from bosun.db.models import SourceRecord, TaskSourceLink

    from .conftest import make_repo

    origin = make_repo(tmp_path / "origin", {"README.md": "# base\n"})
    # Simulate GitHub's pull/<n>/head ref on the remote.
    subprocess.run(["git", "-C", str(origin), "checkout", "-q", "-b", "feature"], check=True)
    (origin / "feature.txt").write_text("from the PR\n")
    subprocess.run(["git", "-C", str(origin), "add", "-A"], check=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(origin),
            "-c",
            "user.name=t",
            "-c",
            "user.email=t@t",
            "commit",
            "-qm",
            "pr",
        ],
        check=True,
    )
    pr_sha = _git(origin, "rev-parse", "HEAD")
    subprocess.run(["git", "-C", str(origin), "update-ref", "refs/pull/7/head", pr_sha], check=True)
    subprocess.run(["git", "-C", str(origin), "checkout", "-q", "main"], check=True)
    clone = tmp_path / "acme-shop"
    subprocess.run(["git", "clone", "-q", str(origin), str(clone)], check=True)
    subprocess.run(
        ["git", "-C", str(clone), "remote", "set-url", "origin", f"{origin}/acme/shop.git"],
        check=True,
    )
    # `origin` must point at the PR's repository; a path ending in acme/shop.git stands in for github.com.
    (origin / "acme").mkdir()
    subprocess.run(
        ["ln", "-s", str(origin / ".git"), str(origin / "acme" / "shop.git")], check=True
    )

    ws = await add_workspace(services, clone)
    task = await add_task(services, ws, title="Review PR 7")
    async with services.db.session() as session:
        record = SourceRecord(
            kind="github_pr",
            scope="conn",
            external_id="acme/shop#7",
            title="PR 7",
            repo="acme/shop",
            number=7,
        )
        session.add(record)
        await session.flush()
        session.add(TaskSourceLink(task_id=task.id, source_record_id=record.id))
        await session.commit()
    run = await services.runs.start(task.id, RunRequest(backend="demo"))
    await wait_for_run(services, run.id)
    row = await _run(services, run.id)
    assert row.base_commit == pr_sha
    assert (Path(row.worktree_path or "") / "feature.txt").exists()
    events = await _events(services, run.id)
    prepared = next(e for e in events if e["type"] == "workspace.prepared")
    assert "pull request head" in prepared["data"]["base"]
