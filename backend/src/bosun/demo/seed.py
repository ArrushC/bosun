"""Optional demonstration dataset.

Creates a small git repository with real TODOs, registers it as a workspace,
scans it with the real scanner, promotes a few discoveries to tasks, adds manual
tasks and launches runs on the **demo backend** through the real orchestration
pipeline. No Claude calls are made and nothing outside Bosun's data
directory is touched. Everything created is flagged `is_demo` and can be removed.
"""

from __future__ import annotations

import contextlib
import shutil
import subprocess
from pathlib import Path
from typing import Any

from sqlalchemy import delete, select

from bosun.db.models import AgentRun, SourceRecord, Task, Workspace
from bosun.domain.enums import (
    ExecutionBackend,
    ExecutionMode,
    TaskKind,
    TaskPriority,
    TaskStatus,
)
from bosun.orchestration.config import RunRequest
from bosun.tasks.service import add_dependency, create_task, import_records

FILES: dict[str, str] = {
    "README.md": """# Acme Orders API

A small order-management service used to demonstrate Bosun.

## Roadmap
- [ ] Publish an OpenAPI description
- [ ] Add request tracing
- [x] Basic CRUD for orders
""",
    "TODO.md": """# Backlog
- Rate-limit the public endpoints
- [ ] Rotate the signing keys automatically
""",
    "src/orders/service.py": '''"""Order service."""

from decimal import Decimal


class OrderService:
    def __init__(self, repo):
        self.repo = repo

    def total(self, order_id: str) -> Decimal:
        order = self.repo.get(order_id)
        # TODO: apply regional tax rules instead of the flat 20% rate
        return sum(line.price * line.qty for line in order.lines) * Decimal("1.2")

    def cancel(self, order_id: str) -> None:
        # FIXME: cancelling an already shipped order should raise, not silently succeed
        self.repo.update(order_id, status="cancelled")
''',
    "src/orders/repo.py": """class OrderRepo:
    def __init__(self):
        self._orders = {}

    def get(self, order_id):
        # HACK: linear scan until the index lands
        for key, order in self._orders.items():
            if key == order_id:
                return order
        raise KeyError(order_id)

    def update(self, order_id, **fields):
        self._orders[order_id].update(fields)
""",
    "web/src/api.ts": """export async function fetchOrders(): Promise<unknown[]> {
  // TODO: retry with exponential backoff on 5xx responses
  const res = await fetch("/api/orders");
  return res.json();
}
""",
    "tests/test_service.py": """def test_placeholder():
    assert True
""",
    "docs/operations.md": """# Operations

TODO: document the on-call escalation path

- [ ] Add a runbook for database failover
""",
}


async def demo_workspace_path(services: Any) -> Path:
    return services.cfg.data_dir / "demo" / "acme-orders"


def _init_repo(root: Path) -> None:
    if root.exists():
        shutil.rmtree(root)
    for rel, content in FILES.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
    env_args = [
        "-c",
        "user.name=Bosun Demo",
        "-c",
        "user.email=demo@bosun.local",
    ]
    subprocess.run(["git", "init", "-q", "-b", "main", str(root)], check=True)
    subprocess.run(["git", "-C", str(root), "add", "-A"], check=True)
    subprocess.run(
        ["git", "-C", str(root), *env_args, "commit", "-q", "-m", "Initial demo project"],
        check=True,
    )


async def seed_demo(services: Any, *, launch_runs: bool = True) -> dict[str, Any]:
    root = await demo_workspace_path(services)
    async with services.db.session() as session:
        existing = await session.scalar(select(Workspace).where(Workspace.is_demo.is_(True)))
        if existing is not None:
            return {"workspace_id": existing.id, "created": False}

    _init_repo(root)
    async with services.db.session() as session:
        workspace = Workspace(
            name="Acme Orders (demo)",
            path=str(root),
            is_git=True,
            default_branch="main",
            is_demo=True,
        )
        session.add(workspace)
        await session.commit()
        workspace_id = workspace.id
    await services.ingestion.scan_workspace(workspace_id)

    async with services.db.session() as session:
        records = (
            await session.scalars(
                select(SourceRecord).where(SourceRecord.workspace_id == workspace_id)
            )
        ).all()
        by_title = {r.title: r for r in records}
        picks = [
            by_title.get("apply regional tax rules instead of the flat 20% rate"),
            by_title.get("cancelling an already shipped order should raise, not silently succeed"),
            by_title.get("retry with exponential backoff on 5xx responses"),
        ]
        imported = await import_records(
            session, [p for p in picks if p is not None], status=TaskStatus.READY
        )
        for task in imported:
            task.is_demo = True

        manual = await create_task(
            session,
            is_demo=True,
            title="Add an order-history endpoint",
            description="Expose `GET /orders/{id}/history` returning status transitions with timestamps.",
            acceptance_criteria="- Returns transitions oldest first\n- 404 for unknown orders\n- Covered by tests",
            status=TaskStatus.READY,
            priority=TaskPriority.HIGH,
            kind=TaskKind.IMPLEMENTATION,
            labels=["api", "feature"],
            workspace_id=workspace_id,
            execution_mode=ExecutionMode.TEAM,
        )
        docs = await create_task(
            session,
            is_demo=True,
            title="Write the database failover runbook",
            description="Operations needs a step-by-step runbook for failing over the primary database.",
            status=TaskStatus.BACKLOG,
            priority=TaskPriority.MEDIUM,
            kind=TaskKind.CHORE,
            labels=["docs", "ops"],
            workspace_id=workspace_id,
        )
        await add_dependency(session, docs, manual)
        await session.commit()
        task_ids = [t.id for t in imported] + [manual.id, docs.id]

    launched = []
    if launch_runs and len(imported) >= 2:
        first = await services.runs.start(
            imported[0].id, RunRequest(backend=ExecutionBackend.DEMO, mode=ExecutionMode.SINGLE)
        )
        team = await services.runs.start(
            manual.id,
            RunRequest(
                backend=ExecutionBackend.DEMO,
                mode=ExecutionMode.TEAM,
                require_plan_approval=False,
                demo_scenario={"needs_approval": True},
            ),
        )
        launched = [first.id, team.id]
    services.bus.notify("task", None, action="created")
    return {
        "workspace_id": workspace_id,
        "task_ids": task_ids,
        "run_ids": launched,
        "created": True,
    }


async def remove_demo(services: Any) -> dict[str, int]:
    async with services.db.session() as session:
        runs = (await session.scalars(select(AgentRun).where(AgentRun.is_demo.is_(True)))).all()
    for run in runs:
        if services.runs.is_active(run.id):
            await services.runs.cancel(run.id)
    await services.runs.wait_idle(timeout=30)
    removed = {"runs": 0, "tasks": 0, "workspaces": 0}
    for run in runs:
        if run.worktree_path and run.integration_status not in ("discarded",):
            with contextlib.suppress(Exception):  # best-effort cleanup
                await services.runs.integrate(run.id, "discard")
    async with services.db.session() as session:
        removed["runs"] = (
            await session.execute(delete(AgentRun).where(AgentRun.is_demo.is_(True)))
        ).rowcount or 0
        removed["tasks"] = (
            await session.execute(delete(Task).where(Task.is_demo.is_(True)))
        ).rowcount or 0
        removed["workspaces"] = (
            await session.execute(delete(Workspace).where(Workspace.is_demo.is_(True)))
        ).rowcount or 0
        await session.commit()
    root = await demo_workspace_path(services)
    if root.exists():
        shutil.rmtree(root, ignore_errors=True)
    services.bus.notify("task", None, action="deleted")
    services.bus.notify("workspace", None, action="deleted")
    return removed
