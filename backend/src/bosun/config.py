"""Process-level configuration loaded from environment variables / `.env`.

Runtime-tunable preferences (provider, concurrency, roles, ...) live in the
database and are edited from the Settings screen; see `settings_store.py`.
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Repository root of this application
APP_ROOT = Path(__file__).resolve().parents[3]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="BOSUN_",
        env_file=(APP_ROOT / ".env", APP_ROOT / "backend" / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    host: str = "127.0.0.1"
    port: int = 8787
    # Opt-in for binding to a non-loopback interface. Requires `auth_token`.
    allow_remote: bool = False
    auth_token: str | None = None
    # Inside a container: listen on all of the container's interfaces without a token,
    # because its port is published on the host's loopback only (see compose.yaml).
    published_on_loopback: bool = False

    data_dir: Path = Field(default=APP_ROOT / ".data")
    database_url: str | None = None

    # Directory registered as the first workspace on a fresh database.
    initial_workspace: Path | None = None
    # Folder holding your projects; the workspace picker starts browsing here.
    projects_dir: Path | None = None

    # Extra origins allowed to call the API (the Vite dev server is always allowed).
    allowed_origins: list[str] = Field(default_factory=list)

    # Claude Code CLI used by the Agent SDK. Defaults to the SDK's bundled binary.
    claude_cli_path: Path | None = None

    # Credentials for explicitly selected non-subscription providers.
    anthropic_api_key: str | None = None
    vertex_project_id: str | None = None
    vertex_region: str | None = None

    github_token: str | None = None
    github_api_url: str = "https://api.github.com"

    log_level: str = "INFO"
    log_json: bool = False

    # Serve the built frontend from FastAPI (single-port mode).
    serve_frontend: bool = True

    @field_validator("initial_workspace", "projects_dir", mode="before")
    @classmethod
    def _empty_path_is_none(cls, value: object) -> object:
        return None if value in ("", None) else value

    @property
    def resolved_database_url(self) -> str:
        if self.database_url:
            return self.database_url
        return f"sqlite+aiosqlite:///{self.data_dir / 'bosun.db'}"

    @property
    def worktrees_dir(self) -> Path:
        return self.data_dir / "worktrees"

    @property
    def frontend_dist(self) -> Path:
        return APP_ROOT / "frontend" / "dist"

    def startup_workspace(self) -> Path | None:
        """The directory registered as the first workspace: configured, or launched from."""
        if self.initial_workspace:
            return self.initial_workspace.expanduser().resolve()
        launched_from = os.environ.get("BOSUN_LAUNCH_CWD")
        if launched_from:
            path = Path(launched_from).resolve()
            # Launched from inside Bosun itself (`scripts/dev.sh`): nothing to register.
            if not path.is_relative_to(APP_ROOT):
                return path
        return None

    def default_initial_workspace(self) -> Path:
        """Where the workspace picker starts browsing."""
        if workspace := self.startup_workspace():
            return workspace
        if self.projects_dir:
            return self.projects_dir.expanduser().resolve()
        return APP_ROOT.parent


def running_in_container() -> bool:
    """Docker creates /.dockerenv and Podman /run/.containerenv in every container."""
    return Path("/.dockerenv").exists() or Path("/run/.containerenv").exists()


@lru_cache
def get_settings() -> Settings:
    return Settings()
