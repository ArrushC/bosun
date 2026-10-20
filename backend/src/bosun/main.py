"""FastAPI application factory."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import func, select

from bosun import __version__
from bosun.agents.claude_sdk import ClaudeAgentAdapter
from bosun.agents.contracts import AgentAdapter
from bosun.agents.fake import FakeAgentAdapter
from bosun.agents.providers import ProviderConfigError, claude_cli_path
from bosun.agents.roles import seed_roles
from bosun.api.routes import (
    approvals,
    dashboard,
    demo,
    github,
    meta,
    runs,
    settings,
    sources,
    tasks,
    workflows,
    workspaces,
    ws,
)
from bosun.config import Settings, get_settings, running_in_container
from bosun.db.migrate import upgrade_to_head
from bosun.db.models import Workspace
from bosun.db.session import Database
from bosun.domain.state_machines import InvalidTransition
from bosun.ingestion.file_walker import is_git_repo
from bosun.ingestion.workspace_scanner import git_branch
from bosun.logging_setup import configure_logging
from bosun.orchestration.manager import RunConflict, RunNotFound
from bosun.security.guard import LocalRequestGuard
from bosun.services import Services, build_services
from bosun.settings_store import load_settings
from bosun.tasks.service import TaskError

log = logging.getLogger(__name__)

API_PREFIX = "/api/v1"
DEV_ORIGINS = ["http://localhost:5173", "http://127.0.0.1:5173"]


async def _register_initial_workspace(services: Services) -> None:
    """On a fresh install, register the directory Bosun was launched from."""
    async with services.db.session() as session:
        if await session.scalar(select(func.count(Workspace.id))):
            return
        root = services.cfg.startup_workspace()
        if root is None or not root.is_dir():
            return
        git = is_git_repo(root)
        workspace = Workspace(
            name=root.name,
            path=str(root),
            is_git=git,
            default_branch=git_branch(root) if git else None,
        )
        session.add(workspace)
        await session.commit()
        workspace_id = workspace.id
    try:
        await services.ingestion.scan_workspace(workspace_id)
    except Exception:  # noqa: BLE001 - a failed first scan must not block startup
        log.exception("Initial workspace scan failed")


def check_bind_address(cfg: Settings, *, in_container: bool) -> None:
    """Bosun executes code: listen beyond loopback only when that cannot expose it."""
    if cfg.host in ("127.0.0.1", "localhost", "::1") or (cfg.allow_remote and cfg.auth_token):
        return
    if cfg.published_on_loopback:
        if in_container:
            return
        raise RuntimeError(
            "BOSUN_PUBLISHED_ON_LOOPBACK only applies inside a container whose port is "
            "published on the host's loopback (see compose.yaml)."
        )
    raise RuntimeError(
        f"Refusing to bind to {cfg.host}: Bosun executes code. Set BOSUN_ALLOW_REMOTE=true and "
        "BOSUN_AUTH_TOKEN to expose it beyond localhost."
    )


def create_app(
    cfg: Settings | None = None,
    *,
    adapters: dict[str, AgentAdapter] | None = None,
    run_migrations: bool = True,
    register_initial_workspace: bool = True,
) -> FastAPI:
    cfg = cfg or get_settings()
    configure_logging(cfg.log_level, cfg.log_json)
    check_bind_address(cfg, in_container=running_in_container())

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        cfg.data_dir.mkdir(parents=True, exist_ok=True)
        if run_migrations:
            await asyncio.to_thread(upgrade_to_head, cfg.resolved_database_url)
        db = Database(cfg.resolved_database_url)
        services = build_services(
            cfg,
            db,
            adapters
            or {
                "claude": ClaudeAgentAdapter(claude_cli_path(cfg)),
                "demo": FakeAgentAdapter(step_delay=0.9),
            },
        )
        app.state.services = services
        async with db.session() as session:
            await seed_roles(session)
            prefs = await load_settings(session)
        await services.limiter.resize(prefs.execution.max_concurrent_sessions)
        recovered = await services.runs.recover()
        if recovered["runs"]:
            log.warning("Marked %s run(s) as interrupted after restart", recovered["runs"])
        await services.ingestion.refresh_workspace_status()
        if register_initial_workspace:
            await _register_initial_workspace(services)
        services.ingestion.start_background_sync()
        log.info("Bosun %s ready on http://%s:%s", __version__, cfg.host, cfg.port)
        try:
            yield
        finally:
            await services.ingestion.stop_background_sync()
            await services.runs.shutdown()
            await db.dispose()

    app = FastAPI(
        title="Bosun",
        version=__version__,
        lifespan=lifespan,
        openapi_url=f"{API_PREFIX}/openapi.json",
        docs_url=f"{API_PREFIX}/docs",
        redoc_url=None,
    )

    for router in (
        meta,
        workspaces,
        sources,
        github,
        tasks,
        runs,
        approvals,
        settings,
        dashboard,
        demo,
        workflows,
        ws,
    ):
        app.include_router(router.router, prefix=API_PREFIX)

    @app.exception_handler(RunConflict)
    async def _conflict(_: Request, exc: RunConflict) -> JSONResponse:
        return JSONResponse({"detail": str(exc)}, status_code=409)

    @app.exception_handler(InvalidTransition)
    async def _transition(_: Request, exc: InvalidTransition) -> JSONResponse:
        return JSONResponse({"detail": str(exc)}, status_code=409)

    @app.exception_handler(RunNotFound)
    async def _not_found(_: Request, exc: RunNotFound) -> JSONResponse:
        return JSONResponse({"detail": str(exc)}, status_code=404)

    @app.exception_handler(TaskError)
    async def _task_error(_: Request, exc: TaskError) -> JSONResponse:
        return JSONResponse({"detail": str(exc)}, status_code=400)

    @app.exception_handler(ProviderConfigError)
    async def _provider(_: Request, exc: ProviderConfigError) -> JSONResponse:
        return JSONResponse({"detail": str(exc)}, status_code=400)

    dist = cfg.frontend_dist
    if cfg.serve_frontend and (dist / "index.html").is_file():
        app.mount("/assets", StaticFiles(directory=dist / "assets"), name="assets")

        @app.get("/{path:path}", include_in_schema=False)
        async def spa(path: str) -> FileResponse:
            if path.startswith("api/"):
                from fastapi import HTTPException

                raise HTTPException(404, "Not found")
            candidate = (dist / path).resolve()
            if path and candidate.is_file() and candidate.is_relative_to(dist.resolve()):
                return FileResponse(candidate)
            return FileResponse(Path(dist / "index.html"))

    origins = [
        *DEV_ORIGINS,
        *cfg.allowed_origins,
        f"http://localhost:{cfg.port}",
        f"http://127.0.0.1:{cfg.port}",
    ]
    extra_hosts = [cfg.host] if cfg.allow_remote else []
    guarded = LocalRequestGuard(
        app, allowed_origins=origins, extra_hosts=extra_hosts, auth_token=cfg.auth_token
    )
    app.state.guarded = guarded
    return app


def create_guarded_app() -> LocalRequestGuard:
    """ASGI entry point used by uvicorn: the app wrapped in the localhost request guard."""
    app = create_app()
    return app.state.guarded
