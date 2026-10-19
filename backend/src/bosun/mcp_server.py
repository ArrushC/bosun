"""Optional MCP interface to a running Bosun instance.

A thin stdio MCP server that calls Bosun's HTTP API, so the core
application is unaffected. Exposes reading tasks and runs, creating tasks and
starting runs. Approvals are deliberately *not* exposed: permission decisions
stay with the human in the Bosun UI.

Usage (e.g. in a Claude Code `.mcp.json`):

    {"mcpServers": {"bosun": {"command": "uv",
      "args": ["--directory", "/path/to/bosun/backend", "run", "bosun-mcp"]}}}
"""

from __future__ import annotations

import os
import uuid
from typing import Any

import httpx

from bosun.config import get_settings


def _client() -> httpx.Client:
    cfg = get_settings()
    base = os.environ.get("BOSUN_URL") or f"http://127.0.0.1:{cfg.port}"
    headers = {"Content-Type": "application/json"}
    if cfg.auth_token:
        headers["Authorization"] = f"Bearer {cfg.auth_token}"
    return httpx.Client(base_url=f"{base}/api/v1", headers=headers, timeout=30)


def _request(
    method: str,
    path: str,
    body: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
) -> Any:
    with _client() as client:
        try:
            response = client.request(method, path, json=body, headers=headers)
        except httpx.TransportError as exc:
            raise RuntimeError(
                "Bosun is not running (start it with `make start` or `make dev`)"
            ) from exc
    if response.status_code >= 400:
        detail = (
            response.json().get("detail")
            if response.headers.get("content-type", "").startswith("application/json")
            else response.text
        )
        raise RuntimeError(f"Bosun returned {response.status_code}: {detail}")
    return response.json() if response.content else None


def _task_summary(task: dict[str, Any]) -> dict[str, Any]:
    run = task.get("latest_run") or {}
    return {
        "id": task["id"],
        "key": task["key"],
        "title": task["title"],
        "status": task["status"],
        "priority": task["priority"],
        "labels": task["labels"],
        "workspace": task.get("workspace_name"),
        "latest_run": {"id": run.get("id"), "status": run.get("status")} if run else None,
    }


def build_server():  # noqa: ANN201 - MCPServer type comes from an optional dependency
    from mcp.server.mcpserver import MCPServer

    server = MCPServer(
        name="bosun",
        title="Bosun",
        instructions=(
            "Read and create tasks and start agent runs in a local Bosun instance. "
            "Starting a run launches Claude Code agents that consume the user's quota: confirm with the "
            "user first. Permission approvals happen in the Bosun UI, not through this server."
        ),
    )

    @server.tool(
        description="List tasks on the board, optionally filtered by status (comma separated) or a search query."
    )
    def list_tasks(status: str | None = None, query: str | None = None) -> list[dict[str, Any]]:
        params = "&".join(f"{k}={v}" for k, v in {"status": status, "q": query}.items() if v)
        return [
            _task_summary(t) for t in _request("GET", f"/tasks{'?' + params if params else ''}")
        ]

    @server.tool(
        description="Create a task in the backlog (or another column). Does not start any agent."
    )
    def create_task(
        title: str,
        description: str = "",
        workspace_id: str | None = None,
        priority: str = "none",
        status: str = "backlog",
        acceptance_criteria: str = "",
    ) -> dict[str, Any]:
        task = _request(
            "POST",
            "/tasks",
            {
                "title": title,
                "description": description,
                "workspace_id": workspace_id,
                "priority": priority,
                "status": status,
                "acceptance_criteria": acceptance_criteria,
            },
        )
        return _task_summary(task)

    @server.tool(description="List workspaces (local project directories) that tasks can run in.")
    def list_workspaces() -> list[dict[str, Any]]:
        return [
            {"id": w["id"], "name": w["name"], "path": w["path"], "is_git": w["is_git"]}
            for w in _request("GET", "/workspaces")
        ]

    @server.tool(
        description=(
            "Start an agent run for a task. mode is single, team or auto. Launches real Claude Code agents "
            "(or the demo backend if backend='demo'); confirm with the user before calling."
        )
    )
    def start_run(task_id: str, mode: str = "single", backend: str = "claude") -> dict[str, Any]:
        run = _request(
            "POST",
            f"/tasks/{task_id}/runs",
            {"mode": mode, "backend": backend},
            headers={"Idempotency-Key": str(uuid.uuid4())},
        )
        return {"run_id": run["id"], "status": run["status"], "task_key": run.get("task_key")}

    @server.tool(description="Get the status, agents, pending approvals and summary of a run.")
    def get_run_status(run_id: str) -> dict[str, Any]:
        run = _request("GET", f"/runs/{run_id}")
        return {
            "id": run["id"],
            "task": f"{run.get('task_key')} {run.get('task_title')}",
            "status": run["status"],
            "status_reason": run.get("status_reason"),
            "strategy": run.get("strategy"),
            "branch": run.get("branch"),
            "agents": [
                {"name": s["name"], "role": s["role"], "status": s["status"]}
                for s in run["sessions"]
            ],
            "subtasks": [
                {"key": s["key"], "title": s["title"], "status": s["status"]}
                for s in run["subtasks"]
            ],
            "pending_approvals": [a["title"] for a in run["approvals"] if a["status"] == "pending"],
            "error": run.get("error"),
            "summary": run.get("summary"),
        }

    @server.tool(
        description="List approval requests waiting for the human operator (resolve them in the Bosun UI)."
    )
    def list_pending_approvals() -> list[dict[str, Any]]:
        return [
            {
                "id": a["id"],
                "task": a.get("task_key"),
                "run_id": a["run_id"],
                "title": a["title"],
                "kind": a["kind"],
            }
            for a in _request("GET", "/approvals")
        ]

    return server


def main() -> None:
    build_server().run("stdio")


if __name__ == "__main__":
    main()
