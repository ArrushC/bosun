"""Authored workflows: validation, running a draft on a task, and the HTTP API."""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from sqlalchemy import select
from starlette.testclient import TestClient

from bosun.agents.contracts import ErrorKind, SessionIO, SessionOutcome, SessionSpec
from bosun.agents.fake import FakeAgentAdapter, _Ctx
from bosun.agents.roles import seed_roles
from bosun.db.migrate import MIGRATIONS_DIR, upgrade_to_head
from bosun.db.models import AgentRole, AgentRun, AgentSession, Subtask, Task, Workflow
from bosun.db.session import Database
from bosun.domain.enums import RunStatus, SubtaskStatus, TaskStatus
from bosun.orchestration.config import RunConfig, RunRequest
from bosun.orchestration.manager import RunConflict
from bosun.services import Services, build_services
from bosun.workflows.spec import WorkflowSpec, WorkflowStep
from bosun.workflows.validation import AgentRef, validate_workflow

from .conftest import add_task, add_workspace, make_repo, wait_for_run

AGENTS = {
    "researcher": AgentRef("Researcher", "read_only", enabled=True, is_coordinator=False),
    "implementer": AgentRef("Implementer", "edit_and_test", enabled=True, is_coordinator=False),
    "reviewer": AgentRef("Reviewer", "read_only", enabled=True, is_coordinator=False),
    "tester": AgentRef("Tester", "edit_and_test", enabled=False, is_coordinator=False),
    "coordinator": AgentRef("Coordinator", "read_only", enabled=True, is_coordinator=True),
}


def step(key: str, agent: str | None = "researcher", *after: str, **fields: Any) -> WorkflowStep:
    return WorkflowStep(
        key=key,
        title=fields.pop("title", key.title()),
        agent=agent,
        instructions=fields.pop("instructions", f"Do {key}."),
        after=list(after),
        **fields,
    )


FIX_WITH_REVIEW = [
    step("research", "researcher", title="Research"),
    step("implement", "implementer", "research", title="Implement", changes_code=True),
    step("test", "tester", "implement", title="Add tests", changes_code=True),
    step("review", "reviewer", "implement", title="Review"),
    step("final", "reviewer", "test", "review", title="Final check"),
]


def codes(steps: list[WorkflowStep]) -> list[tuple[str, str, list[str]]]:
    issues = validate_workflow(WorkflowSpec(steps=steps), AGENTS)
    return [(i.level, i.code, i.steps) for i in issues]


# -- validation ---------------------------------------------------------------------------
def test_an_empty_workflow_cannot_run() -> None:
    assert codes([]) == [("error", "no_steps", [])]


def test_a_valid_workflow_has_no_problems() -> None:
    steps = [step("a", "researcher"), step("b", "implementer", "a", changes_code=True)]
    assert codes(steps) == []


@pytest.mark.parametrize(
    ("agent", "changes_code", "code"),
    [
        (None, False, "missing_agent"),
        ("ghost", False, "unknown_agent"),
        ("tester", False, "disabled_agent"),
        ("coordinator", False, "coordinator_agent"),
        ("researcher", True, "read_only_changes_code"),
    ],
)
def test_agent_problems_are_errors_never_substitutions(
    agent: str | None, changes_code: bool, code: str
) -> None:
    assert codes([step("a", agent, changes_code=changes_code)]) == [("error", code, ["a"])]


def test_dependency_problems() -> None:
    assert ("error", "self_dependency", ["a"]) in codes([step("a", "researcher", "a")])
    assert ("error", "unknown_dependency", ["a"]) in codes([step("a", "researcher", "gone")])
    loop = validate_workflow(
        WorkflowSpec(
            steps=[step("a", "researcher", "b", title="A"), step("b", "researcher", "a", title="B")]
        ),
        AGENTS,
    )
    assert [(i.code, sorted(i.steps)) for i in loop] == [("cycle", ["a", "b"])]
    assert "loop: " in loop[0].message and "A" in loop[0].message and "B" in loop[0].message


def test_warnings_do_not_block() -> None:
    parallel = [
        step("a", "implementer", changes_code=True),
        step("b", "implementer", changes_code=True),
        step("c", "implementer", "a", "b", changes_code=True),
    ]
    assert codes(parallel) == [("warning", "parallel_code_changes", ["a", "b"])]
    assert codes([step("a", "researcher", instructions="")]) == [
        ("warning", "missing_instructions", ["a"])
    ]


def test_names_and_size_limits() -> None:
    assert ("error", "missing_title", ["a"]) in codes([step("a", "researcher", title=" ")])
    many = [step(f"s{i}", "researcher") for i in range(21)]
    assert ("error", "too_many_steps", []) in codes(many)


# -- running on the team engine -------------------------------------------------------------
class RecordingAdapter(FakeAgentAdapter):
    """The demo adapter, recording each session's spec; it can fail chosen steps once."""

    def __init__(self, step_delay: float = 0) -> None:
        super().__init__(step_delay=step_delay)
        self.specs: list[SessionSpec] = []
        self.fail_once: set[str] = set()

    async def run(self, spec: SessionSpec, io: SessionIO) -> SessionOutcome:
        self.specs.append(spec)
        failing = next((t for t in self.fail_once if f": {t}\n" in spec.prompt), None)
        if failing is not None:
            self.fail_once.discard(failing)
            return await _Ctx(self, spec, io).failure("failing", ErrorKind.PROCESS_CRASH)
        return await super().run(spec, io)


@pytest.fixture
async def recorded(settings) -> AsyncIterator[tuple[Services, RecordingAdapter]]:  # noqa: ANN001
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    await asyncio.to_thread(upgrade_to_head, settings.resolved_database_url)
    db = Database(settings.resolved_database_url)
    adapter = RecordingAdapter()
    services = build_services(settings, db, {"demo": adapter, "claude": adapter})
    async with db.session() as session:
        await seed_roles(session)
    yield services, adapter
    await services.runs.shutdown(timeout=5)
    await db.dispose()


async def add_workflow(
    services: Services, steps: list[WorkflowStep], name: str = "Fix with review"
) -> str:
    async with services.db.session() as session:
        row = Workflow(name=name, spec=WorkflowSpec(steps=steps).model_dump(mode="json"))
        session.add(row)
        await session.commit()
        return row.id


async def run_workflow(
    services: Services, repo: Path, steps: list[WorkflowStep], **request: Any
) -> tuple[str, str]:
    ws = await add_workspace(services, repo)
    task = await add_task(services, ws, title="Weekend surcharge applied twice")
    workflow_id = await add_workflow(services, steps)
    run = await services.runs.start(
        task.id, RunRequest(backend="demo", workflow_id=workflow_id, **request)
    )
    return run.id, task.id


async def state(
    services: Services, run_id: str
) -> tuple[AgentRun, dict[str, Subtask], list[AgentSession]]:
    async with services.db.session() as session:
        run = await session.get(AgentRun, run_id)
        assert run is not None
        subtasks = (await session.scalars(select(Subtask).where(Subtask.run_id == run_id))).all()
        sessions = (
            await session.scalars(
                select(AgentSession)
                .where(AgentSession.run_id == run_id)
                .order_by(AgentSession.created_at)
            )
        ).all()
    return run, {s.key: s for s in subtasks}, list(sessions)


async def set_enabled(services: Services, key: str, enabled: bool) -> None:
    async with services.db.session() as session:
        await session.execute(
            sa.update(AgentRole).where(AgentRole.key == key).values(enabled=enabled)
        )
        await session.commit()


async def test_a_workflow_runs_its_steps_in_order_in_parallel_and_at_joins(
    recorded: tuple[Services, RecordingAdapter], repo: Path
) -> None:
    services, _ = recorded
    run_id, task_id = await run_workflow(services, repo, FIX_WITH_REVIEW)
    await wait_for_run(services, run_id, timeout=40)
    run, steps, sessions = await state(services, run_id)

    assert run.status == RunStatus.COMPLETED, run.error
    assert (run.mode, run.strategy, run.workflow_id) == ("workflow", "workflow", run.workflow_id)
    assert run.workflow_id
    assert all(s.status == SubtaskStatus.COMPLETED for s in steps.values())
    assert [s.kind for s in sessions] == ["agent"] * 5  # no coordinator, no planning
    events = await services.bus.history(run_id=run_id, limit=10_000)
    plan = next(e for e in events if e["type"] == "plan.created")
    assert plan["session_id"] is None and plan["data"]["source"] == "workflow"
    assert plan["data"]["workflow"]["name"] == "Fix with review"
    order = [(e["data"]["key"], e["data"]["to"]) for e in events if e["type"] == "subtask.status"]
    started = {k: i for i, (k, to) in enumerate(order) if to == "running"}
    done = {k: i for i, (k, to) in enumerate(order) if to == "completed"}
    assert done["research"] < started["implement"] < done["implement"]
    assert max(started["test"], started["review"]) < min(done["test"], done["review"])
    assert started["final"] > max(done["test"], done["review"])
    assert run.summary and "### Research" in run.summary and "### Final check" in run.summary
    async with services.db.session() as session:
        assert (await session.get(Task, task_id)).status == TaskStatus.REVIEW  # type: ignore[union-attr]


async def test_steps_get_the_workflow_wording_their_overrides_and_earlier_reports(
    recorded: tuple[Services, RecordingAdapter], repo: Path
) -> None:
    services, adapter = recorded
    steps = [
        step("research", "researcher", title="Research"),
        step(
            "implement",
            "implementer",
            "research",
            title="Implement",
            changes_code=True,
            model="haiku",
            max_turns=7,
            timeout_minutes=3,
        ),
    ]
    run_id, _ = await run_workflow(services, repo, steps)
    await wait_for_run(services, run_id, timeout=40)
    implement = next(s for s in adapter.specs if s.role == "implementer")
    assert '## Your step in the workflow "Fix with review": Implement' in implement.prompt
    assert "## Reports from earlier steps" in implement.prompt
    assert "### Research (Researcher)" in implement.prompt
    assert "coordinator" not in implement.prompt.lower()
    assert (implement.model, implement.max_turns, implement.timeout_s) == ("haiku", 7, 180.0)
    research = next(s for s in adapter.specs if s.role == "researcher")
    assert research.max_turns != 7 and research.timeout_s != 180.0


async def test_a_failed_step_marks_what_did_not_run_and_says_so(
    recorded: tuple[Services, RecordingAdapter], repo: Path
) -> None:
    services, adapter = recorded
    adapter.fail_once = {"Check the API"}
    steps = [
        step("left", "researcher", title="Check the API", retries=0),
        step("left2", "reviewer", "left", title="Summarise API findings"),
        step("right", "reviewer", title="Check the docs"),
        step("right2", "reviewer", "right", title="Summarise doc findings"),
    ]
    run_id, task_id = await run_workflow(services, repo, steps)
    await wait_for_run(services, run_id, timeout=40)
    run, by_key, _ = await state(services, run_id)

    assert {k: s.status for k, s in by_key.items()} == {
        "left": SubtaskStatus.FAILED,
        "left2": SubtaskStatus.SKIPPED,
        "right": SubtaskStatus.COMPLETED,
        "right2": SubtaskStatus.COMPLETED,
    }
    assert by_key["left2"].error == "Not run: it waits for Check the API, which did not finish."
    assert run.status == RunStatus.FAILED
    assert run.error == (
        "Check the API failed: The agent process exited unexpectedly (simulated). "
        "Not run: Summarise API findings."
    )
    assert run.error_kind == ErrorKind.PROCESS_CRASH
    async with services.db.session() as session:
        assert (await session.get(Task, task_id)).status == TaskStatus.BLOCKED  # type: ignore[union-attr]

    # Retrying runs the failed step again and the steps that waited for it.
    await services.runs.retry(run_id)
    await wait_for_run(services, run_id, timeout=40)
    run, by_key, sessions = await state(services, run_id)
    assert run.status == RunStatus.COMPLETED, run.error
    assert all(s.status == SubtaskStatus.COMPLETED for s in by_key.values())
    assert sum(1 for s in sessions if s.subtask_id == by_key["right"].id) == 1  # not repeated


async def test_pause_and_resume_continue_only_unfinished_steps(
    recorded: tuple[Services, RecordingAdapter], repo: Path
) -> None:
    services, adapter = recorded
    adapter.step_delay = 0.05
    run_id, _ = await run_workflow(services, repo, FIX_WITH_REVIEW)
    for _ in range(400):
        _, _, sessions = await state(services, run_id)
        if any(
            s.role == "implementer" and s.sdk_session_id and s.status == "running" for s in sessions
        ):
            break
        await asyncio.sleep(0.01)
    await services.runs.pause(run_id)
    await wait_for_run(services, run_id, timeout=20)
    assert (await state(services, run_id))[0].status == RunStatus.PAUSED
    adapter.step_delay = 0
    await services.runs.resume(run_id)
    await wait_for_run(services, run_id, timeout=40)
    run, _, sessions = await state(services, run_id)
    assert run.status == RunStatus.COMPLETED, run.error
    assert [s.role for s in sessions].count("researcher") == 1


async def test_invalid_workflows_are_refused_with_a_reason(services: Services, repo: Path) -> None:
    ws = await add_workspace(services, repo)
    task = await add_task(services, ws, title="Refused")
    await set_enabled(services, "tester", False)
    for steps, reason in [
        ([step("a", "ghost", title="Look")], "Look: There is no agent called 'ghost'"),
        ([step("a", "researcher", title="Edit", changes_code=True)], "can only read files"),
        ([step("a", "tester", title="Test")], "The Tester agent is turned off"),
        ([], "Add a step to run this workflow"),
    ]:
        workflow_id = await add_workflow(services, steps)
        with pytest.raises(RunConflict, match=reason):
            await services.runs.start(task.id, RunRequest(backend="demo", workflow_id=workflow_id))
    async with services.db.session() as session:
        assert not (await session.scalars(select(AgentRun))).all()


async def test_editing_the_draft_does_not_change_a_started_run(
    recorded: tuple[Services, RecordingAdapter], repo: Path
) -> None:
    services, adapter = recorded
    adapter.step_delay = 0.05
    steps = [step("a", "researcher", title="First"), step("b", "reviewer", "a", title="Second")]
    run_id, _ = await run_workflow(services, repo, steps)
    async with services.db.session() as session:
        run = await session.get(AgentRun, run_id)
        assert run is not None
        workflow = await session.get(Workflow, run.workflow_id)
        assert workflow is not None
        workflow.spec = WorkflowSpec(steps=[step("x", "reviewer", title="Replaced")]).model_dump(
            mode="json"
        )
        workflow.revision += 1
        await session.commit()
    await wait_for_run(services, run_id, timeout=40)
    run, by_key, _ = await state(services, run_id)
    assert sorted(by_key) == ["a", "b"]
    assert run.config["workflow"]["revision"] == 1


def test_older_run_configs_still_load() -> None:
    old = {
        "mode": "team",
        "backend": "demo",
        "provider": "subscription",
        "team_roles": ["implementer"],
    }
    config = RunConfig.model_validate(old)
    assert config.mode == "team" and config.workflow is None


def test_the_migration_keeps_existing_runs(tmp_path: Path) -> None:
    url = f"sqlite:///{tmp_path / 'old.db'}"
    alembic = Config()
    alembic.set_main_option("script_location", str(MIGRATIONS_DIR))
    alembic.set_main_option("sqlalchemy.url", url)
    command.upgrade(alembic, "0001")
    engine = sa.create_engine(url)
    with engine.begin() as conn:
        conn.execute(
            sa.text(
                "INSERT INTO tasks (id, number, title, description, status, priority, kind, labels,"
                " acceptance_criteria, agent_instructions, execution_mode, attachments, position,"
                " archived, is_demo, created_at, updated_at) VALUES ('tsk_1', 1, 'T', '', 'review',"
                " 'none', 'implementation', '[]', '', '', 'single', '[]', 0, 0, 0, '2026-10-01', '2026-10-01')"
            )
        )
        conn.execute(
            sa.text(
                "INSERT INTO agent_runs (id, task_id, number, mode, status, backend, provider, isolation,"
                " config, usage, integration_status, is_demo, created_at, updated_at) VALUES ('run_1',"
                " 'tsk_1', 1, 'team', 'completed', 'demo', 'demo', 'worktree', '{}', '{}', 'pending', 0,"
                " '2026-10-01', '2026-10-01')"
            )
        )
    command.upgrade(alembic, "head")
    with engine.begin() as conn:
        row = conn.execute(sa.text("SELECT mode, workflow_id FROM agent_runs")).one()
        assert tuple(row) == ("team", None)
        assert conn.execute(sa.text("SELECT count(*) FROM workflows")).scalar() == 0
    engine.dispose()


# -- HTTP API -------------------------------------------------------------------------------
def wait_run(client: TestClient, run_id: str) -> dict[str, Any]:
    deadline = time.monotonic() + 40
    while time.monotonic() < deadline:
        run = client.get(f"/api/v1/runs/{run_id}").json()
        if not run["active"] and run["status"] in ("completed", "failed", "cancelled"):
            return run
        time.sleep(0.05)
    raise AssertionError("run did not finish")


def spec_json(steps: list[WorkflowStep]) -> dict[str, Any]:
    return WorkflowSpec(steps=steps).model_dump(mode="json")


def test_workflow_drafts_save_with_revisions(client: TestClient) -> None:
    created = client.post("/api/v1/workflows", json={"name": "Fix with review"})
    assert created.status_code == 201, created.text
    wf = created.json()
    assert wf["revision"] == 1 and [i["code"] for i in wf["issues"]] == ["no_steps"]

    saved = client.patch(
        f"/api/v1/workflows/{wf['id']}",
        json={"revision": 1, "spec": spec_json([step("a", "researcher")])},
    )
    assert (
        saved.status_code == 200 and saved.json()["revision"] == 2 and saved.json()["issues"] == []
    )
    stale = client.patch(f"/api/v1/workflows/{wf['id']}", json={"revision": 1, "name": "Other tab"})
    assert stale.status_code == 409 and "changed somewhere else" in stale.json()["detail"]

    listed = client.get("/api/v1/workflows").json()
    assert [(w["name"], w["steps"], w["run_count"]) for w in listed] == [("Fix with review", 1, 0)]
    assert client.delete(f"/api/v1/workflows/{wf['id']}").status_code == 204
    assert client.get(f"/api/v1/workflows/{wf['id']}").status_code == 404
    assert client.get("/api/v1/workflows").json() == []


def test_running_a_workflow_on_a_new_task(client: TestClient, tmp_path: Path) -> None:
    repo = make_repo(tmp_path / "shop")
    ws = client.post("/api/v1/workspaces", json={"path": str(repo)}).json()
    wf = client.post(
        "/api/v1/workflows",
        json={
            "name": "Fix",
            "spec": spec_json(
                [step("a", "researcher"), step("b", "implementer", "a", changes_code=True)]
            ),
        },
    ).json()
    body = {
        "revision": 1,
        "new_task": {
            "title": "Surcharge twice",
            "description": "Fix it.",
            "workspace_id": ws["id"],
        },
    }
    first = client.post(
        f"/api/v1/workflows/{wf['id']}/runs", json=body, headers={"Idempotency-Key": "k-1"}
    )
    assert first.status_code == 201, first.text
    again = client.post(
        f"/api/v1/workflows/{wf['id']}/runs", json=body, headers={"Idempotency-Key": "k-1"}
    )
    assert again.json()["id"] == first.json()["id"]
    tasks = client.get("/api/v1/tasks").json()
    assert [t["title"] for t in tasks] == ["Surcharge twice"]  # one task, not two

    run = wait_run(client, first.json()["id"])
    assert run["status"] == "completed" and run["mode"] == "workflow"
    assert run["config"]["workflow"]["name"] == "Fix"
    listed = client.get(f"/api/v1/runs?workflow_id={wf['id']}").json()
    assert [(r["id"], r["workflow_name"]) for r in listed] == [(run["id"], "Fix")]
    summary = client.get("/api/v1/workflows").json()[0]
    assert summary["run_count"] == 1 and summary["last_run"]["id"] == run["id"]


def test_running_a_workflow_is_refused_clearly(client: TestClient, tmp_path: Path) -> None:
    repo = make_repo(tmp_path / "shop")
    ws = client.post("/api/v1/workspaces", json={"path": str(repo)}).json()
    task = client.post("/api/v1/tasks", json={"title": "Existing", "workspace_id": ws["id"]}).json()
    bad = client.post(
        "/api/v1/workflows",
        json={"name": "Bad", "spec": spec_json([step("a", None, title="Look")])},
    ).json()
    refused = client.post(
        f"/api/v1/workflows/{bad['id']}/runs", json={"revision": 1, "task_id": task["id"]}
    )
    assert refused.status_code == 422
    assert (
        refused.json()["detail"]
        == "Fix this workflow before running it. Look: Choose an agent for this step."
    )

    good = client.post(
        "/api/v1/workflows", json={"name": "Good", "spec": spec_json([step("a")])}
    ).json()
    stale = client.post(
        f"/api/v1/workflows/{good['id']}/runs", json={"revision": 9, "task_id": task["id"]}
    )
    assert stale.status_code == 409
    both = client.post(
        f"/api/v1/workflows/{good['id']}/runs",
        json={
            "revision": 1,
            "task_id": task["id"],
            "new_task": {"title": "x", "workspace_id": ws["id"]},
        },
    )
    assert both.status_code == 422
    on_task = client.post(
        f"/api/v1/workflows/{good['id']}/runs", json={"revision": 1, "task_id": task["id"]}
    )
    assert on_task.status_code == 201 and on_task.json()["task_id"] == task["id"]


def test_a_new_task_is_removed_if_its_run_cannot_start(client: TestClient, tmp_path: Path) -> None:
    repo = make_repo(tmp_path / "shop")
    ws = client.post("/api/v1/workspaces", json={"path": str(repo)}).json()
    wf = client.post(
        "/api/v1/workflows", json={"name": "Fix", "spec": spec_json([step("a")])}
    ).json()
    services: Services = client.app.app.state.services  # type: ignore[attr-defined]

    async def refuse(*_: Any, **__: Any) -> Any:
        raise RunConflict("No agent roles are enabled.")

    services.runs.start = refuse  # type: ignore[method-assign]
    response = client.post(
        f"/api/v1/workflows/{wf['id']}/runs",
        json={"revision": 1, "new_task": {"title": "Left behind?", "workspace_id": ws["id"]}},
    )
    assert response.status_code == 409
    assert client.get("/api/v1/tasks").json() == []
