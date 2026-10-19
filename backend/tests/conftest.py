from __future__ import annotations

import asyncio
import subprocess
from collections.abc import AsyncIterator, Iterator
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from bosun.agents.fake import FakeAgentAdapter
from bosun.agents.roles import seed_roles
from bosun.config import Settings
from bosun.db.migrate import upgrade_to_head
from bosun.db.models import Task, Workspace
from bosun.db.session import Database
from bosun.main import create_app
from bosun.services import Services, build_services
from bosun.tasks.service import create_task


def make_settings(tmp_path: Path) -> Settings:
    return Settings(
        data_dir=tmp_path / "data",
        database_url=f"sqlite+aiosqlite:///{tmp_path / 'data' / 'test.db'}",
        serve_frontend=False,
        github_token="test-token",
        github_api_url="https://api.github.test",
        _env_file=None,  # type: ignore[call-arg]
    )


def make_repo(root: Path, files: dict[str, str] | None = None) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    for rel, content in (
        files or {"README.md": "# Demo\n", "src/app.py": "# TODO: implement\n"}
    ).items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
    subprocess.run(["git", "init", "-q", "-b", "main", str(root)], check=True)
    subprocess.run(["git", "-C", str(root), "add", "-A"], check=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(root),
            "-c",
            "user.name=t",
            "-c",
            "user.email=t@t",
            "commit",
            "-q",
            "-m",
            "init",
        ],
        check=True,
    )
    return root


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return make_settings(tmp_path)


@pytest.fixture
async def services(settings: Settings) -> AsyncIterator[Services]:
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    await asyncio.to_thread(upgrade_to_head, settings.resolved_database_url)
    db = Database(settings.resolved_database_url)
    svc = build_services(
        settings,
        db,
        {"demo": FakeAgentAdapter(step_delay=0), "claude": FakeAgentAdapter(step_delay=0)},
    )
    async with db.session() as session:
        await seed_roles(session)
    yield svc
    await svc.runs.shutdown(timeout=5)
    await db.dispose()


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    return make_repo(tmp_path / "repo")


async def add_workspace(services: Services, path: Path, *, trusted: bool = False) -> str:
    async with services.db.session() as session:
        workspace = Workspace(
            name=path.name, path=str(path), is_git=(path / ".git").exists(), trusted=trusted
        )
        session.add(workspace)
        await session.commit()
        return workspace.id


async def add_task(services: Services, workspace_id: str | None, **fields: object) -> Task:
    async with services.db.session() as session:
        task = await create_task(
            session, title=fields.pop("title", "Do the thing"), workspace_id=workspace_id, **fields
        )
        await session.commit()
        return task


async def wait_for_run(services: Services, run_id: str, timeout: float = 20.0) -> None:
    ctx = services.runs.context(run_id)
    if ctx is not None and ctx.task is not None:
        await asyncio.wait_for(asyncio.shield(ctx.task), timeout)


@pytest.fixture
def client(settings: Settings) -> Iterator[TestClient]:
    app = create_app(
        settings,
        adapters={"demo": FakeAgentAdapter(step_delay=0), "claude": FakeAgentAdapter(step_delay=0)},
        register_initial_workspace=False,
    )
    with TestClient(app.state.guarded, base_url="http://localhost") as test_client:
        yield test_client
