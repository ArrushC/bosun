"""HTTP API, request guard and WebSocket delivery through the real ASGI stack."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from starlette.testclient import TestClient

from .conftest import make_repo

JSON = {"content-type": "application/json"}


def wait_run(client: TestClient, run_id: str, timeout: float = 20) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        run = client.get(f"/api/v1/runs/{run_id}").json()
        if not run["active"] and run["status"] in (
            "completed",
            "failed",
            "cancelled",
            "paused",
            "interrupted",
        ):
            return run
        time.sleep(0.05)
    raise AssertionError(f"run {run_id} did not finish")


def workspace(client: TestClient, path: Path) -> dict[str, Any]:
    res = client.post("/api/v1/workspaces", json={"path": str(path)})
    assert res.status_code == 201, res.text
    return res.json()


# -- guard ---------------------------------------------------------------------------
def test_guard_rejects_foreign_host_and_cross_site_writes(client: TestClient) -> None:
    assert client.get("/api/v1/health").status_code == 200
    assert client.get("/api/v1/health", headers={"host": "evil.example"}).status_code == 403
    cross = client.post(
        "/api/v1/tasks", json={"title": "x"}, headers={"origin": "https://evil.example"}
    )
    assert cross.status_code == 403
    form = client.post(
        "/api/v1/tasks",
        content="title=x",
        headers={"content-type": "application/x-www-form-urlencoded"},
    )
    assert form.status_code == 403
    ok = client.post(
        "/api/v1/tasks",
        json={"title": "x"},
        headers={"origin": "http://localhost:5173", "host": "localhost"},
    )
    assert ok.status_code == 201


def test_guard_rejects_cross_site_websocket(client: TestClient) -> None:
    from starlette.websockets import WebSocketDisconnect

    try:
        with client.websocket_connect(
            "/api/v1/ws", headers={"origin": "https://evil.example", "host": "localhost"}
        ) as ws:
            ws.receive_json()
    except WebSocketDisconnect as exc:
        assert exc.code == 4403
    else:
        raise AssertionError("cross-site websocket was accepted")


# -- tasks & board ----------------------------------------------------------------------
def test_task_crud_board_moves_and_dependencies(client: TestClient) -> None:
    a = client.post(
        "/api/v1/tasks",
        json={
            "title": "Design API",
            "priority": "high",
            "labels": ["api"],
            "acceptance_criteria": "- documented",
        },
    ).json()
    b = client.post(
        "/api/v1/tasks", json={"title": "Implement API", "depends_on": [a["id"]]}
    ).json()
    assert a["key"] == "BOS-1" and b["key"] == "BOS-2"
    assert b["depends_on"] == [a["id"]] and b["open_dependencies"] == 1

    # Cycles are rejected.
    cycle = client.post(f"/api/v1/tasks/{a['id']}/dependencies", json={"depends_on_id": b["id"]})
    assert cycle.status_code == 400 and "cycle" in cycle.json()["detail"]

    moved = client.post(f"/api/v1/tasks/{a['id']}/move", json={"status": "ready"}).json()
    assert moved["status"] == "ready"
    done = client.post(f"/api/v1/tasks/{a['id']}/move", json={"status": "done"}).json()
    assert done["status"] == "done" and done["completed_at"]
    assert client.get(f"/api/v1/tasks/{b['id']}").json()["open_dependencies"] == 0

    # Ordering within a column via neighbours.
    c = client.post("/api/v1/tasks", json={"title": "Write docs", "status": "ready"}).json()
    d = client.post("/api/v1/tasks", json={"title": "Release", "status": "ready"}).json()
    client.post(f"/api/v1/tasks/{c['id']}/move", json={"status": "ready", "after_id": d["id"]})
    ready = [t["id"] for t in client.get("/api/v1/tasks?status=ready").json()]
    assert ready.index(d["id"]) < ready.index(c["id"])

    patched = client.patch(
        f"/api/v1/tasks/{b['id']}",
        json={"title": "Implement the API", "labels": ["api", "backend"]},
    ).json()
    assert patched["title"] == "Implement the API"
    detail = client.get(f"/api/v1/tasks/{b['id']}").json()
    assert detail["dependencies"][0]["key"] == "BOS-1"
    assert any(a["kind"] == "updated" for a in detail["activity"])
    assert client.get("/api/v1/tasks/labels").json() == ["api", "backend"]

    assert client.delete(f"/api/v1/tasks/{d['id']}").status_code == 204
    assert d["id"] not in [t["id"] for t in client.get("/api/v1/tasks").json()]


def test_discover_and_import_todos(client: TestClient, tmp_path: Path) -> None:
    repo = make_repo(
        tmp_path / "proj",
        {
            "src/a.py": "# TODO: handle retries\n# FIXME: leaks sockets\n",
            "README.md": "- [ ] ship it\n",
        },
    )
    ws = workspace(client, repo)
    assert ws["is_git"] and ws["last_scan_summary"]["items"] == 3
    page = client.get(f"/api/v1/sources/records?workspace_id={ws['id']}").json()
    assert page["total"] == 3 and page["counts"]["untriaged"] == 3
    fixme = next(r for r in page["items"] if r["tag"] == "FIXME")
    # Discovery never creates tasks on its own.
    assert client.get("/api/v1/tasks").json() == []

    tasks = client.post(
        "/api/v1/sources/records/import", json={"record_ids": [fixme["id"]], "status": "ready"}
    ).json()
    assert (
        tasks[0]["kind"] == "bugfix"
        and tasks[0]["priority"] == "high"
        and tasks[0]["status"] == "ready"
    )
    assert tasks[0]["sources"][0]["path"] == "src/a.py" and tasks[0]["workspace_id"] == ws["id"]
    # Importing again returns the existing task instead of a duplicate.
    again = client.post("/api/v1/sources/records/import", json={"record_ids": [fixme["id"]]}).json()
    assert again[0]["id"] == tasks[0]["id"]
    assert client.get(f"/api/v1/sources/records?workspace_id={ws['id']}").json()["counts"] == {
        "untriaged": 2,
        "imported": 1,
        "dismissed": 0,
    }

    other = next(r for r in page["items"] if r["tag"] is None)
    client.post("/api/v1/sources/records/dismiss", json={"record_ids": [other["id"]]})
    assert (
        client.get(f"/api/v1/sources/records?workspace_id={ws['id']}").json()["counts"]["dismissed"]
        == 1
    )

    # Rescan after the FIXME is fixed: the record resolves and the task is told.
    (repo / "src/a.py").write_text("# TODO: handle retries\n")
    client.post(f"/api/v1/workspaces/{ws['id']}/scan")
    task = client.get(f"/api/v1/tasks/{tasks[0]['id']}").json()
    assert task["source_state"] == "resolved"


# -- runs ---------------------------------------------------------------------------
def test_run_lifecycle_over_http(client: TestClient, tmp_path: Path) -> None:
    ws = workspace(client, make_repo(tmp_path / "proj"))
    task = client.post(
        "/api/v1/tasks", json={"title": "Add notes", "workspace_id": ws["id"]}
    ).json()
    preview = client.post(
        f"/api/v1/tasks/{task['id']}/runs/preview", json={"backend": "demo"}
    ).json()
    assert preview["can_start"] and preview["workspace"]["isolation"] == "worktree"
    assert "implementer" in preview["scope"] and "Edit" in preview["scope"]["implementer"]["tools"]

    headers = {"Idempotency-Key": "abc-123"}
    first = client.post(
        f"/api/v1/tasks/{task['id']}/runs", json={"backend": "demo"}, headers=headers
    )
    second = client.post(
        f"/api/v1/tasks/{task['id']}/runs", json={"backend": "demo"}, headers=headers
    )
    assert first.status_code == 201 and second.json()["id"] == first.json()["id"]
    run = wait_run(client, first.json()["id"])
    assert run["status"] == "completed" and run["sessions"][0]["status"] == "completed"
    assert run["config"]["scope"]["implementer"]["profile"] == "edit_and_test"

    events = client.get(f"/api/v1/runs/{run['id']}/events").json()
    assert events["events"][0]["type"] == "run.created" and not events["has_more"]
    page = client.get(
        f"/api/v1/runs/{run['id']}/events?after_seq={events['events'][3]['seq']}"
    ).json()
    assert page["events"][0]["seq"] == events["events"][4]["seq"]

    diff = client.get(f"/api/v1/runs/{run['id']}/diff").json()
    assert diff["available"] and diff["files"]
    assert (
        client.post(f"/api/v1/runs/{run['id']}/integrate", json={"action": "merge"}).status_code
        == 400
    )  # needs confirm
    merged = client.post(
        f"/api/v1/runs/{run['id']}/integrate", json={"action": "merge", "confirm": True}
    )
    assert merged.status_code == 200, merged.text

    transcript = client.get(
        f"/api/v1/runs/{run['id']}/sessions/{run['sessions'][0]['id']}/transcript"
    ).json()
    assert any(i["type"] == "tool.call" for i in transcript["items"])

    listed = client.get("/api/v1/runs").json()
    assert listed[0]["id"] == run["id"] and listed[0]["roles"] == ["implementer"]
    assert client.get(f"/api/v1/tasks/{task['id']}").json()["status"] == "review"


def test_run_conflicts_return_409(client: TestClient, tmp_path: Path) -> None:
    ws = workspace(client, make_repo(tmp_path / "proj"))
    task = client.post("/api/v1/tasks", json={"title": "x", "workspace_id": ws["id"]}).json()
    run = client.post(
        f"/api/v1/tasks/{task['id']}/runs",
        json={"backend": "demo", "demo_scenario": {"needs_approval": True}},
    ).json()
    dup = client.post(f"/api/v1/tasks/{task['id']}/runs", json={"backend": "demo"})
    assert dup.status_code == 409
    # Done is a human decision that cannot be made while a run is active.
    assert (
        client.post(f"/api/v1/tasks/{task['id']}/move", json={"status": "done"}).status_code == 409
    )
    deadline = time.monotonic() + 10
    pending: list[dict[str, Any]] = []
    while time.monotonic() < deadline and not pending:
        pending = client.get(f"/api/v1/approvals?run_id={run['id']}").json()
        time.sleep(0.05)
    assert pending and pending[0]["tool_name"] == "Bash" and pending[0]["task_key"] == "BOS-1"
    resolved = client.post(
        f"/api/v1/approvals/{pending[0]['id']}/resolve", json={"decision": "approve"}
    )
    assert resolved.json()["status"] == "approved"
    assert (
        client.post(
            f"/api/v1/approvals/{pending[0]['id']}/resolve", json={"decision": "deny"}
        ).status_code
        == 409
    )
    assert wait_run(client, run["id"])["status"] == "completed"
    no_ws = client.post("/api/v1/tasks", json={"title": "floating"}).json()
    assert (
        client.post(f"/api/v1/tasks/{no_ws['id']}/runs", json={"backend": "demo"}).status_code
        == 409
    )


# -- websocket --------------------------------------------------------------------------
def test_websocket_live_delivery_and_replay(client: TestClient, tmp_path: Path) -> None:
    ws_row = workspace(client, make_repo(tmp_path / "proj"))
    task = client.post("/api/v1/tasks", json={"title": "x", "workspace_id": ws_row["id"]}).json()
    with client.websocket_connect(
        "/api/v1/ws", headers={"origin": "http://localhost:5173", "host": "localhost"}
    ) as ws:
        hello = ws.receive_json()
        assert hello["kind"] == "hello"
        ws.send_json({"type": "subscribe", "topics": ["runs", "entities"]})
        run = client.post(f"/api/v1/tasks/{task['id']}/runs", json={"backend": "demo"}).json()
        live: list[dict[str, Any]] = []
        while True:
            msg = ws.receive_json()
            if msg["kind"] == "event":
                live.append(msg["event"])
                if msg["event"]["type"] == "run.completed":
                    break
        seqs = [e["seq"] for e in live]
        assert seqs == sorted(seqs) and len(set(seqs)) == len(seqs)

    # A reconnecting client replays everything after its last seen sequence number.
    with client.websocket_connect(
        "/api/v1/ws", headers={"origin": "http://localhost:5173", "host": "localhost"}
    ) as ws:
        ws.receive_json()
        ws.send_json({"type": "subscribe", "topics": []})
        cut = seqs[len(seqs) // 2]
        ws.send_json({"type": "replay", "run_id": run["id"], "after_seq": cut})
        replayed = []
        while True:
            msg = ws.receive_json()
            if msg["kind"] == "replay_done":
                break
            if msg["kind"] == "event":
                replayed.append(msg["event"]["seq"])
        assert replayed == [s for s in seqs if s > cut]
        assert msg["last_seq"] == seqs[-1]


# -- settings, roles, dashboard, demo ---------------------------------------------------
def test_settings_roles_dashboard_and_graph(client: TestClient) -> None:
    settings = client.get("/api/v1/settings").json()
    assert settings["provider"]["provider"] == "subscription"
    settings["execution"]["max_concurrent_sessions"] = 5
    updated = client.put("/api/v1/settings", json=settings).json()
    assert updated["execution"]["max_concurrent_sessions"] == 5

    roles = client.get("/api/v1/roles").json()
    assert roles[0]["key"] == "coordinator" and {r["key"] for r in roles} >= {
        "researcher",
        "implementer",
        "tester",
        "reviewer",
    }
    coordinator = roles[0]
    assert (
        client.patch(
            f"/api/v1/roles/{coordinator['id']}", json={"permission_profile": "edit"}
        ).status_code
        == 400
    )
    custom = client.post(
        "/api/v1/roles", json={"key": "docs", "name": "Docs writer", "permission_profile": "edit"}
    ).json()
    assert client.delete(f"/api/v1/roles/{custom['id']}").status_code == 204
    assert client.delete(f"/api/v1/roles/{coordinator['id']}").status_code == 400

    dash = client.get("/api/v1/dashboard").json()
    assert set(dash["tasks_by_status"]) == {
        "backlog",
        "ready",
        "in_progress",
        "review",
        "blocked",
        "done",
    }
    assert len(dash["throughput"]) == 14
    assert client.get("/api/v1/graph/overview").json() == {"nodes": [], "edges": []}
    browse = client.get("/api/v1/fs/browse").json()
    assert browse["path"] and isinstance(browse["entries"], list)


def test_demo_seed_and_removal(client: TestClient) -> None:
    seeded = client.post("/api/v1/demo?launch_runs=false").json()
    assert seeded["created"] and len(seeded["task_ids"]) == 5
    tasks = client.get("/api/v1/tasks").json()
    assert all(t["is_demo"] for t in tasks) and {t["status"] for t in tasks} >= {"ready", "backlog"}
    assert client.post("/api/v1/demo").json()["created"] is False  # idempotent
    removed = client.delete("/api/v1/demo").json()
    assert removed["tasks"] == 5 and removed["workspaces"] == 1
    assert client.get("/api/v1/tasks").json() == []
