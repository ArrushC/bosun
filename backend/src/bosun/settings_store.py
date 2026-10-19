"""Runtime preferences persisted in the database (edited from the Settings screen)."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bosun.db.models import SettingEntry
from bosun.domain.enums import ExecutionBackend, ExecutionMode, Provider

EffortLevel = Literal["low", "medium", "high", "xhigh", "max"]


class _Settings(BaseModel):
    model_config = ConfigDict(json_schema_serialization_defaults_required=True)


DEFAULT_SAFE_BASH = [
    "Bash(git status:*)",
    "Bash(git diff:*)",
    "Bash(git log:*)",
    "Bash(git show:*)",
    "Bash(ls:*)",
    "Bash(pwd)",
]

DEFAULT_TEST_COMMANDS = [
    "Bash(pytest:*)",
    "Bash(python -m pytest:*)",
    "Bash(python3 -m pytest:*)",
    "Bash(uv run pytest:*)",
    "Bash(npm test:*)",
    "Bash(npm run test:*)",
    "Bash(npx vitest run:*)",
    "Bash(npx jest:*)",
    "Bash(yarn test:*)",
    "Bash(pnpm test:*)",
    "Bash(go test:*)",
    "Bash(cargo test:*)",
    "Bash(make test:*)",
    "Bash(ctest:*)",
]

DEFAULT_EXCLUDES = [
    "node_modules/",
    ".git/",
    "dist/",
    "build/",
    "out/",
    "target/",
    "vendor/",
    "third_party/",
    ".venv/",
    "venv/",
    "__pycache__/",
    ".mypy_cache/",
    ".pytest_cache/",
    ".next/",
    ".nuxt/",
    "coverage/",
    ".remember/",
    ".claude/",
    ".idea/",
    ".vscode/",
    ".cache/",
    ".tox/",
    ".gradle/",
    ".terraform/",
    ".svelte-kit/",
    ".turbo/",
    ".data/",
    "*.min.js",
    "*.min.css",
    "*.map",
    "*.lock",
    "package-lock.json",
    "*.pb.go",
    "*_pb2.py",
    "*.generated.*",
    "*.g.dart",
]


class ProviderSettings(_Settings):
    provider: Provider = Provider.SUBSCRIPTION
    # None = Claude Code's default model for the selected provider.
    model: str | None = None
    effort: EffortLevel | None = None
    vertex_project_id: str | None = None
    vertex_region: str | None = None


class ExecutionSettings(_Settings):
    backend: ExecutionBackend = ExecutionBackend.CLAUDE
    default_mode: ExecutionMode = ExecutionMode.SINGLE
    max_concurrent_sessions: int = Field(default=3, ge=1, le=12)
    max_parallel_per_run: int = Field(default=2, ge=1, le=6)
    max_team_size: int = Field(default=4, ge=1, le=8)
    default_max_turns: int = Field(default=60, ge=1, le=500)
    session_timeout_minutes: int = Field(default=45, ge=1, le=600)
    approval_timeout_minutes: int = Field(default=30, ge=1, le=1440)
    require_plan_approval: bool = True
    subtask_max_attempts: int = Field(default=2, ge=1, le=5)
    allow_sdk_subagents: bool = False
    allow_web_tools: bool = False
    commit_agent_changes: bool = True
    # Run Bash inside Claude Code's OS sandbox (bubblewrap on Linux, Seatbelt on macOS).
    sandbox_bash: bool = False
    auto_allow_bash: list[str] = Field(default_factory=lambda: list(DEFAULT_SAFE_BASH))
    test_commands: list[str] = Field(default_factory=lambda: list(DEFAULT_TEST_COMMANDS))


class ScanSettings(_Settings):
    tags: list[str] = Field(default_factory=lambda: ["TODO", "FIXME", "HACK", "XXX", "BUG"])
    exclude_globs: list[str] = Field(default_factory=lambda: list(DEFAULT_EXCLUDES))
    max_file_kb: int = Field(default=512, ge=1, le=10_240)
    max_files: int = Field(default=50_000, ge=100)
    include_markdown_tasks: bool = True
    include_todo_files: bool = True


class GraphSettings(_Settings):
    direction: Literal["LR", "TB"] = "LR"
    aggregate_tools: bool = True
    animate_edges: bool = True
    show_minimap: bool = True


class GitHubSettings(_Settings):
    # External writes (comments, pushes, pull requests) stay disabled unless enabled here,
    # and each individual write additionally requires confirmation in the UI.
    write_enabled: bool = False
    default_sync_interval_minutes: int = Field(default=0, ge=0, le=1440)


class AppSettings(_Settings):
    provider: ProviderSettings = Field(default_factory=ProviderSettings)
    execution: ExecutionSettings = Field(default_factory=ExecutionSettings)
    scan: ScanSettings = Field(default_factory=ScanSettings)
    graph: GraphSettings = Field(default_factory=GraphSettings)
    github: GitHubSettings = Field(default_factory=GitHubSettings)
    onboarding_completed: bool = False


_KEY = "app"


async def load_settings(session: AsyncSession) -> AppSettings:
    entry = await session.get(SettingEntry, _KEY)
    if entry is None or not entry.value:
        return AppSettings()
    return AppSettings.model_validate(entry.value)


async def save_settings(session: AsyncSession, settings: AppSettings) -> AppSettings:
    entry = await session.get(SettingEntry, _KEY)
    payload = settings.model_dump(mode="json")
    if entry is None:
        session.add(SettingEntry(key=_KEY, value=payload))
    else:
        entry.value = payload
    await session.commit()
    return settings


async def get_value(session: AsyncSession, key: str) -> object | None:
    entry = await session.scalar(select(SettingEntry).where(SettingEntry.key == key))
    return entry.value if entry else None


async def set_value(session: AsyncSession, key: str, value: object) -> None:
    entry = await session.get(SettingEntry, key)
    if entry is None:
        session.add(SettingEntry(key=key, value=value))
    else:
        entry.value = value
    await session.commit()
