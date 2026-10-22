"""Workflow versions and templates: publishing, immutability, restoring, running a version,
starting from a template, launching from a task, and upgrading milestone 1 data."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from starlette.testclient import TestClient

from bosun.db.migrate import MIGRATIONS_DIR
from bosun.orchestration.config import RunConfig
from bosun.services import Services
from bosun.workflows.templates import TEMPLATES

from .conftest import make_repo
from .test_approval_steps import approval
from .test_workflows import spec_json, step, wait_run

API = "/api/v1"


def services_of(client: TestClient) -> Services:
    return client.app.app.state.services  # type: ignore[attr-defined]


def new_workflow(client: TestClient, steps: list[Any], name: str = "Fix") -> dict[str, Any]:
    created = client.post(f"{API}/workflows", json={"name": name, "spec": spec_json(steps)})
    assert created.status_code == 201, created.text
    return created.json()


def publish(client: TestClient, wf: dict[str, Any], note: str = "") -> Any:
    return client.post(
        f"{API}/workflows/{wf['id']}/versions", json={"revision": wf["revision"], "note": note}
    )


def edit(client: TestClient, wf: dict[str, Any], **fields: Any) -> dict[str, Any]:
    saved = client.patch(f"{API}/workflows/{wf['id']}", json={"revision": wf["revision"], **fields})
    assert saved.status_code == 200, saved.text
    return saved.json()


def set_role(client: TestClient, key: str, **fields: Any) -> None:
    role = next(r for r in client.get(f"{API}/roles").json() if r["key"] == key)
    response = client.patch(f"{API}/roles/{role['id']}", json=fields)
    assert response.status_code == 200, response.text


def workspace(client: TestClient, tmp_path: Path) -> dict[str, Any]:
    repo = make_repo(tmp_path / "shop")
    return client.post(f"{API}/workspaces", json={"path": str(repo)}).json()


# -- publishing -------------------------------------------------------------------------------
def test_publishing_freezes_the_draft_and_its_agents(client: TestClient) -> None:
    wf = new_workflow(client, [step("a", "researcher", title="Look")])
    assert wf["latest_version"] is None and wf["unpublished_changes"] is True

    v1 = publish(client, wf, note="First cut")
    assert v1.status_code == 201, v1.text
    v1 = v1.json()
    assert (v1["number"], v1["note"], v1["is_latest"], v1["draft_revision"]) == (
        1,
        "First cut",
        True,
        1,
    )
    assert [a["key"] for a in v1["agents"]] == ["researcher"]
    assert v1["agent_changes"] == [] and v1["issues"] == []
    wf = client.get(f"{API}/workflows/{wf['id']}").json()
    assert wf["latest_version"]["number"] == 1 and wf["unpublished_changes"] is False

    # Nothing new: refused, so version numbers mean something.
    same = publish(client, wf)
    assert same.status_code == 409 and "Nothing has changed since version 1" in same.text

    wf = edit(client, wf, spec=spec_json([step("a", "researcher", title="Look harder")]))
    assert wf["unpublished_changes"] is True
    stale = client.post(
        f"{API}/workflows/{wf['id']}/versions", json={"revision": wf["revision"] - 1}
    )
    assert stale.status_code == 409 and "changed somewhere else" in stale.text
    v2 = publish(client, wf).json()
    assert v2["number"] == 2 and v2["spec"]["steps"][0]["title"] == "Look harder"
    old = client.get(f"{API}/workflows/{wf['id']}/versions/1").json()
    assert old["spec"]["steps"][0]["title"] == "Look" and old["is_latest"] is False
    listed = client.get(f"{API}/workflows/{wf['id']}/versions").json()
    assert [(v["number"], v["steps"]) for v in listed] == [(2, 1), (1, 1)]
    assert client.get(f"{API}/workflows/{wf['id']}/versions/9").status_code == 404

    # A changed agent is worth a new version even when the steps are the same.
    set_role(client, "researcher", system_prompt="Be brief.")
    wf = client.get(f"{API}/workflows/{wf['id']}").json()
    changes = client.get(f"{API}/workflows/{wf['id']}/versions/2").json()["agent_changes"]
    assert changes == [
        {"key": "researcher", "name": "Researcher", "state": "changed", "fields": ["instructions"]}
    ]
    assert publish(client, wf).json()["number"] == 3


def test_a_draft_with_problems_cannot_be_published(client: TestClient) -> None:
    wf = new_workflow(client, [step("a", None, title="Look")])
    refused = publish(client, wf)
    assert refused.status_code == 422
    assert (
        refused.json()["detail"]
        == "Fix this workflow before publishing it. Look: Choose an agent for this step."
    )
    assert client.get(f"{API}/workflows/{wf['id']}/versions").json() == []


def test_published_versions_cannot_be_changed_even_directly(client: TestClient) -> None:
    wf = new_workflow(client, [step("a")])
    publish(client, wf)
    engine = sa.create_engine(
        services_of(client).cfg.resolved_database_url.replace("+aiosqlite", "")
    )
    with engine.begin() as conn:
        for statement in (
            "UPDATE workflow_versions SET name = 'x'",
            "DELETE FROM workflow_versions",
        ):
            with pytest.raises(sa.exc.DatabaseError, match="immutable"):
                conn.execute(sa.text(statement))
    engine.dispose()
    assert client.get(f"{API}/workflows/{wf['id']}/versions/1").json()["name"] == "Fix"


def test_restoring_a_version_replaces_only_the_draft(client: TestClient) -> None:
    wf = new_workflow(client, [step("a", title="One")], name="First name")
    publish(client, wf)
    wf = edit(client, wf, name="Second name", spec=spec_json([step("a", title="Two")]))
    publish(client, wf)
    wf = edit(client, wf, spec=spec_json([step("a", title="Three")]))

    stale = client.post(
        f"{API}/workflows/{wf['id']}/versions/1/restore", json={"revision": wf["revision"] - 1}
    )
    assert stale.status_code == 409
    restored = client.post(
        f"{API}/workflows/{wf['id']}/versions/1/restore", json={"revision": wf["revision"]}
    ).json()
    assert restored["name"] == "First name"
    assert restored["spec"]["steps"][0]["title"] == "One"
    assert restored["revision"] == wf["revision"] + 1
    assert restored["latest_version"]["number"] == 2 and restored["unpublished_changes"] is True
    versions = {n: client.get(f"{API}/workflows/{wf['id']}/versions/{n}").json() for n in (1, 2)}
    assert [versions[n]["spec"]["steps"][0]["title"] for n in (1, 2)] == ["One", "Two"]


# -- running versions -------------------------------------------------------------------------
def test_a_version_runs_with_the_agents_it_froze(client: TestClient, tmp_path: Path) -> None:
    ws = workspace(client, tmp_path)
    set_role(client, "researcher", system_prompt="Original researcher instructions.")
    wf = new_workflow(client, [step("a", "researcher", title="Look", instructions="Look v1.")])
    publish(client, wf)
    set_role(client, "researcher", system_prompt="Changed researcher instructions.")
    wf = edit(
        client, wf, spec=spec_json([step("a", "researcher", title="Look", instructions="Look v2.")])
    )

    def run(version: int | None, title: str) -> dict[str, Any]:
        body: dict[str, Any] = {
            "revision": wf["revision"],
            "new_task": {"title": title, "workspace_id": ws["id"]},
        }
        if version:
            body["version"] = version
        started = client.post(f"{API}/workflows/{wf['id']}/runs", json=body)
        assert started.status_code == 201, started.text
        return wait_run(client, started.json()["id"])

    v1 = run(1, "Run version 1")
    assert v1["status"] == "completed" and v1["workflow_version"] == 1
    assert v1["config"]["workflow"]["version"] == 1
    assert "Look v1." in v1["sessions"][0]["prompt"]
    roles = RunConfig.model_validate(_stored_config(client, v1["id"])).roles
    assert roles["researcher"].system_prompt == "Original researcher instructions."

    draft = run(None, "Run the draft")
    assert draft["workflow_version"] is None and draft["config"]["workflow"]["version"] is None
    roles = RunConfig.model_validate(_stored_config(client, draft["id"])).roles
    assert roles["researcher"].system_prompt == "Changed researcher instructions."
    listed = client.get(f"{API}/runs?workflow_id={wf['id']}").json()
    assert sorted((r["workflow_version"] or 0) for r in listed) == [0, 1]
    assert client.get(f"{API}/workflows/{wf['id']}/versions").json()[0]["run_count"] == 1


def _stored_config(client: TestClient, run_id: str) -> dict[str, Any]:
    engine = sa.create_engine(
        services_of(client).cfg.resolved_database_url.replace("+aiosqlite", "")
    )
    with engine.begin() as conn:
        raw = conn.execute(
            sa.text("SELECT config FROM agent_runs WHERE id = :id"), {"id": run_id}
        ).scalar_one()
    engine.dispose()
    return json.loads(raw)


def test_a_version_whose_agent_was_turned_off_is_refused(
    client: TestClient, tmp_path: Path
) -> None:
    ws = workspace(client, tmp_path)
    task = client.post(f"{API}/tasks", json={"title": "T", "workspace_id": ws["id"]}).json()
    wf = new_workflow(client, [step("a", "researcher", title="Look")])
    publish(client, wf)
    set_role(client, "researcher", enabled=False)
    version = client.get(f"{API}/workflows/{wf['id']}/versions/1").json()
    assert [(c["key"], c["state"]) for c in version["agent_changes"]] == [("researcher", "off")]
    assert [(i["code"], i["steps"]) for i in version["issues"]] == [("agent_off", ["a"])]
    refused = client.post(
        f"{API}/workflows/{wf['id']}/runs",
        json={"revision": wf["revision"], "version": 1, "task_id": task["id"]},
    )
    assert refused.status_code == 422
    assert refused.json()["detail"] == (
        "Fix this workflow before running it. Look: The Researcher agent is turned off. "
        "Turn it on in Settings, or publish a new version without it."
    )
    missing = client.post(
        f"{API}/workflows/{wf['id']}/runs",
        json={"revision": wf["revision"], "version": 4, "task_id": task["id"]},
    )
    assert missing.status_code == 404


def test_editing_and_publishing_leave_a_started_run_unchanged(
    client: TestClient, tmp_path: Path
) -> None:
    ws = workspace(client, tmp_path)
    steps = [
        step("research", "researcher", title="Research", instructions="Research v1."),
        approval("approve", "research", title="Approve"),
        step("implement", "implementer", "approve", title="Implement", changes_code=True),
    ]
    wf = new_workflow(client, steps)
    publish(client, wf)
    started = client.post(
        f"{API}/workflows/{wf['id']}/runs",
        json={
            "revision": wf["revision"],
            "version": 1,
            "new_task": {"title": "T", "workspace_id": ws["id"]},
        },
    ).json()
    run_id = started["id"]
    before = _waiting_run(client, run_id)

    # Rework the draft completely, publish it, then restore and publish again.
    wf = edit(
        client,
        wf,
        name="Renamed",
        spec=spec_json([step("other", "reviewer", title="Something else", instructions="New.")]),
    )
    publish(client, wf)
    wf = client.get(f"{API}/workflows/{wf['id']}").json()
    set_role(client, "implementer", system_prompt="Different now.")

    after = client.get(f"{API}/runs/{run_id}").json()
    keep = ("config", "subtasks", "workflow_version", "status")
    assert {k: after[k] for k in keep} == {k: before[k] for k in keep}
    assert after["config"]["workflow"]["name"] == "Fix"

    pending = next(a for a in after["approvals"] if a["status"] == "pending")
    client.post(f"{API}/approvals/{pending['id']}/resolve", json={"decision": "approve"})
    done = wait_run(client, run_id)
    assert done["status"] == "completed", done["error"]
    assert [s["key"] for s in done["subtasks"]] == ["research", "approve", "implement"]
    research = next(s for s in done["sessions"] if s["name"].endswith("Research"))
    assert "Research v1." in research["prompt"]
    roles = RunConfig.model_validate(_stored_config(client, run_id)).roles
    assert roles["implementer"].system_prompt != "Different now."


def _waiting_run(client: TestClient, run_id: str) -> dict[str, Any]:
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        run = client.get(f"{API}/runs/{run_id}").json()
        if run["status"] == "waiting_approval" and any(
            a["status"] == "pending" and a["kind"] == "workflow_step" for a in run["approvals"]
        ):
            return run
        time.sleep(0.05)
    raise AssertionError("the run never waited for approval")


def test_an_approval_decision_through_the_api(client: TestClient, tmp_path: Path) -> None:
    ws = workspace(client, tmp_path)
    wf = new_workflow(
        client,
        [step("a", "researcher", title="Look"), approval("ok", "a", title="Check")],
    )
    run_id = client.post(
        f"{API}/workflows/{wf['id']}/runs",
        json={"revision": wf["revision"], "new_task": {"title": "T", "workspace_id": ws["id"]}},
    ).json()["id"]
    run = _waiting_run(client, run_id)
    pending = client.get(f"{API}/approvals?status=pending").json()
    assert [(a["kind"], a["run_status"], a["title"]) for a in pending] == [
        ("workflow_step", "waiting_approval", "Check")
    ]
    approval_id = pending[0]["id"]
    approved = client.post(
        f"{API}/approvals/{approval_id}/resolve", json={"decision": "approve", "message": "Fine"}
    )
    assert approved.status_code == 200 and approved.json()["status"] == "approved"
    again = client.post(f"{API}/approvals/{approval_id}/resolve", json={"decision": "deny"})
    assert again.status_code == 409 and "already approved" in again.text
    run = wait_run(client, run_id)
    assert run["status"] == "completed"
    assert [(s["key"], s["kind"], s["status"]) for s in run["subtasks"]] == [
        ("a", "agent", "completed"),
        ("ok", "approval", "completed"),
    ]


# -- launching from a task ----------------------------------------------------------------------
def test_running_a_workflow_version_from_a_task(client: TestClient, tmp_path: Path) -> None:
    ws = workspace(client, tmp_path)
    task = client.post(
        f"{API}/tasks", json={"title": "From the board", "workspace_id": ws["id"]}
    ).json()
    wf = new_workflow(
        client, [step("a", "researcher", title="Look"), approval("ok", "a", title="Check it")]
    )
    publish(client, wf)
    set_role(client, "researcher", model="claude-haiku-5-5")
    body = {"workflow_id": wf["id"], "workflow_version": 1, "backend": "demo"}

    preview = client.post(f"{API}/tasks/{task['id']}/runs/preview", json=body).json()
    assert preview["can_start"] is True and preview["errors"] == []
    assert preview["config"]["mode"] == "workflow"
    assert preview["config"]["workflow"]["version"] == 1
    assert any("waits for your approval at Check it" in w for w in preview["warnings"])
    assert any(
        w
        == "Version 1 runs the Researcher agent as it was when it was published. Its model changed since."
        for w in preview["warnings"]
    )
    assert list(preview["scope"]) == ["researcher"]
    assert preview["scope"]["researcher"]["model"] is None  # the frozen agent, not today's

    started = client.post(f"{API}/tasks/{task['id']}/runs", json=body)
    assert started.status_code == 201, started.text
    run = started.json()
    assert (run["mode"], run["workflow_id"], run["workflow_version"]) == ("workflow", wf["id"], 1)
    assert run["task_id"] == task["id"]

    stale = client.post(
        f"{API}/tasks/{task['id']}/runs/preview",
        json={"workflow_id": wf["id"], "workflow_revision": wf["revision"] + 5},
    ).json()
    assert stale["can_start"] is False and "draft changed" in stale["errors"][0]


# -- templates --------------------------------------------------------------------------------
def test_templates_make_independent_drafts(client: TestClient, tmp_path: Path) -> None:
    listed = client.get(f"{API}/workflow-templates").json()
    assert [t["key"] for t in listed] == [
        "fix-with-review",
        "research-report",
        "implement-and-test",
    ]
    fix = listed[0]
    assert fix["outline"] == "Research → Implement → Add tests and Review → Final check"
    assert {(i["level"], i["done"]) for i in fix["setup"]} >= {
        ("required", False)
    }  # no workspace yet
    assert "Add a workspace" in next(i["message"] for i in fix["setup"] if not i["done"])

    first = client.post(f"{API}/workflows", json={"name": "Mine", "template": "fix-with-review"})
    second = client.post(f"{API}/workflows", json={"name": "Yours", "template": "fix-with-review"})
    assert first.status_code == second.status_code == 201
    first, second = first.json(), second.json()
    assert first["template"] == "fix-with-review" and first["issues"] == []
    assert first["spec"] == fix["spec"] and first["description"] == fix["description"]

    edit(client, first, spec=spec_json([step("only", "researcher", title="Changed")]))
    assert client.get(f"{API}/workflows/{second['id']}").json()["spec"] == fix["spec"]
    assert client.get(f"{API}/workflow-templates").json()[0]["spec"] == fix["spec"]
    assert [s.title for s in TEMPLATES[0].steps][0] == "Research"
    assert (
        client.post(f"{API}/workflows", json={"name": "X", "template": "nope"}).status_code == 404
    )

    workspace(client, tmp_path)
    set_role(client, "tester", enabled=False)
    setup = client.get(f"{API}/workflow-templates").json()[0]["setup"]
    missing = [i["message"] for i in setup if i["level"] == "required" and not i["done"]]
    assert missing == [
        "Turn on the Tester agent in Settings, or choose another agent for its steps."
    ]
    third = client.post(f"{API}/workflows", json={"name": "Later", "template": "fix-with-review"})
    assert [(i["code"], i["steps"]) for i in third.json()["issues"]] == [
        ("disabled_agent", ["add-tests"])
    ]


def test_every_template_is_a_valid_runnable_workflow(client: TestClient, tmp_path: Path) -> None:
    ws = workspace(client, tmp_path)
    for t in client.get(f"{API}/workflow-templates").json():
        assert all(i["done"] for i in t["setup"] if i["level"] == "required"), t["key"]
        wf = client.post(f"{API}/workflows", json={"name": t["name"], "template": t["key"]}).json()
        assert wf["issues"] == [], (t["key"], wf["issues"])
        started = client.post(
            f"{API}/workflows/{wf['id']}/runs",
            json={
                "revision": wf["revision"],
                "new_task": {"title": t["name"], "workspace_id": ws["id"]},
            },
        )
        run = wait_run(client, started.json()["id"])
        assert run["status"] == "completed", (t["key"], run["error"])


# -- upgrading milestone 1 data -------------------------------------------------------------------
M1_SPEC = {
    "steps": [
        {"key": "a", "title": "Look", "agent": "researcher", "instructions": "Look.", "after": []},
        {"key": "b", "title": "Fix", "agent": "implementer", "changes_code": True, "after": ["a"]},
    ]
}
M1_CONFIG = {
    "mode": "workflow",
    "backend": "demo",
    "provider": "subscription",
    "team_roles": ["researcher", "implementer"],
    "workflow": {"id": "wf_1", "name": "M1 flow", "revision": 3, "steps": M1_SPEC["steps"]},
}


def test_milestone_1_workflows_and_runs_upgrade_without_loss(tmp_path: Path) -> None:
    url = f"sqlite:///{tmp_path / 'm1.db'}"
    alembic = Config()
    alembic.set_main_option("script_location", str(MIGRATIONS_DIR))
    alembic.set_main_option("sqlalchemy.url", url)
    command.upgrade(alembic, "0002")
    engine = sa.create_engine(url)
    now = "2026-10-21 10:00:00.000000"
    with engine.begin() as conn:
        conn.execute(
            sa.text(
                "INSERT INTO workflows (id, name, description, spec, revision, archived, created_at,"
                " updated_at) VALUES ('wf_1', 'M1 flow', '', :spec, 3, 0, :now, :now)"
            ),
            {"spec": json.dumps(M1_SPEC), "now": now},
        )
        conn.execute(
            sa.text(
                "INSERT INTO tasks (id, number, title, description, status, priority, kind, labels,"
                " acceptance_criteria, agent_instructions, execution_mode, attachments, position,"
                " archived, is_demo, created_at, updated_at) VALUES ('tsk_1', 1, 'T', '', 'review',"
                " 'none', 'implementation', '[]', '', '', 'single', '[]', 0, 0, 0, :now, :now)"
            ),
            {"now": now},
        )
        conn.execute(
            sa.text(
                "INSERT INTO agent_runs (id, task_id, number, mode, strategy, workflow_id, status,"
                " backend, provider, isolation, config, usage, integration_status, is_demo,"
                " created_at, updated_at) VALUES ('run_1', 'tsk_1', 1, 'workflow', 'workflow',"
                " 'wf_1', 'completed', 'demo', 'demo', 'worktree', :config, '{}', 'pending', 0,"
                " :now, :now)"
            ),
            {"config": json.dumps(M1_CONFIG), "now": now},
        )
        for position, key in enumerate(["a", "b"]):
            conn.execute(
                sa.text(
                    "INSERT INTO subtasks (id, run_id, key, position, title, role, instructions,"
                    " depends_on, modifies_code, status, attempts, max_attempts, created_at,"
                    " updated_at) VALUES (:id, 'run_1', :key, :position, :key, 'researcher', '',"
                    " '[]', 0, 'completed', 1, 2, :now, :now)"
                ),
                {"id": f"sub_{key}", "key": key, "position": position, "now": now},
            )
    before = _dump(engine)

    command.upgrade(alembic, "head")
    with engine.begin() as conn:
        assert conn.execute(sa.text("SELECT template FROM workflows")).scalar() is None
        assert conn.execute(sa.text("SELECT count(*) FROM workflow_versions")).scalar() == 0
        assert tuple(
            conn.execute(sa.text("SELECT workflow_id, workflow_version FROM agent_runs")).one()
        ) == ("wf_1", None)
        assert conn.execute(sa.text("SELECT kind FROM subtasks")).scalars().all() == [
            "agent",
            "agent",
        ]
        config = json.loads(conn.execute(sa.text("SELECT config FROM agent_runs")).scalar_one())
    run_config = RunConfig.model_validate(config)
    assert run_config.workflow is not None and run_config.workflow.version is None
    assert [s.kind for s in run_config.workflow.steps] == ["agent", "agent"]
    after = _dump(engine)
    for table in ("workflows", "agent_runs", "subtasks"):
        for old, new in zip(before[table], after[table], strict=True):
            assert {k: new[k] for k in old} == old, table  # every old value kept

    # Rolling back loses only what milestone 2 added.
    command.downgrade(alembic, "0002")
    assert _dump(engine) == before
    engine.dispose()


def _dump(engine: sa.Engine) -> dict[str, list[dict[str, Any]]]:
    with engine.begin() as conn:
        return {
            table: [dict(r._mapping) for r in conn.execute(sa.text(f"SELECT * FROM {table}"))]
            for table in ("workflows", "agent_runs", "subtasks")
        }
