"""Adapter-agnostic contract between orchestration and agent backends.

An `AgentAdapter` executes one agent session described by a `SessionSpec`,
reporting what it observes through `SessionIO` as normalised `AgentEvent`s.
The real implementation drives Claude Code through the Claude Agent SDK; a
deterministic fake implements the same contract for tests and demo mode.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from bosun.domain.enums import Provider, SessionStatus


class ErrorKind:
    AUTH_REQUIRED = "auth_required"
    USAGE_LIMIT = "usage_limit"
    RATE_LIMITED = "rate_limited"
    BILLING = "billing"
    BILLING_GUARD = "billing_guard"
    CLI_MISSING = "cli_missing"
    SANDBOX_UNAVAILABLE = "sandbox_unavailable"
    PROCESS_CRASH = "process_crash"
    TIMEOUT = "timeout"
    MAX_TURNS = "max_turns"
    INVALID_REQUEST = "invalid_request"
    SERVER_ERROR = "server_error"
    PERMISSION_DENIED = "permission_denied"
    WORKSPACE = "workspace"
    CANCELLED = "cancelled"
    UNKNOWN = "unknown"

    # Errors that a retry without human intervention cannot fix.
    NON_RETRYABLE = frozenset(
        {
            AUTH_REQUIRED,
            USAGE_LIMIT,
            BILLING,
            BILLING_GUARD,
            CLI_MISSING,
            SANDBOX_UNAVAILABLE,
            INVALID_REQUEST,
            WORKSPACE,
            CANCELLED,
        }
    )


@dataclass
class ProviderRuntime:
    provider: Provider
    label: str
    # Environment overrides for the Claude Code subprocess (merged over os.environ).
    env: dict[str, str]
    # Acceptable values of the init frame's `apiKeySource`; anything else aborts the run.
    expected_api_key_sources: frozenset[str]
    # Acceptable `provider` values reported in ResultMessage.model_usage.
    expected_usage_providers: frozenset[str]


@dataclass
class SubagentSpec:
    description: str
    prompt: str
    tools: list[str]
    model: str | None = None


@dataclass
class SessionSpec:
    session_id: str
    run_id: str
    task_id: str
    role: str
    prompt: str
    system_prompt_append: str
    cwd: Path
    allowed_roots: list[Path]
    tools: list[str]
    auto_allow: list[str]
    disallowed_tools: list[str]
    provider: ProviderRuntime
    model: str | None = None
    effort: str | None = None
    max_turns: int | None = None
    timeout_s: float = 45 * 60
    resume_sdk_session_id: str | None = None
    output_schema: dict[str, Any] | None = None
    subagents: dict[str, SubagentSpec] | None = None
    setting_sources: list[str] = field(default_factory=list)
    # Run Bash inside Claude Code's OS-level sandbox.
    sandbox_bash: bool = False
    # Never reachable through the shell even outside $HOME (source checkout, app data).
    protected_roots: list[Path] = field(default_factory=list)
    # Free-form hints for the fake adapter (scenario selection); ignored by Claude.
    scenario: dict[str, Any] = field(default_factory=dict)


@dataclass
class AgentEvent:
    type: str
    data: dict[str, Any] = field(default_factory=dict)
    status: str | None = None


@dataclass
class PermissionRequest:
    tool_name: str
    tool_input: dict[str, Any]
    tool_use_id: str | None = None
    title: str | None = None
    description: str | None = None
    decision_reason: str | None = None
    blocked_path: str | None = None
    subagent_id: str | None = None


@dataclass
class PermissionDecision:
    allow: bool
    message: str = ""
    updated_input: dict[str, Any] | None = None
    interrupt: bool = False


@dataclass
class SessionOutcome:
    status: SessionStatus
    sdk_session_id: str | None = None
    result: str | None = None
    structured_output: Any = None
    usage: dict[str, Any] = field(default_factory=dict)
    cost_usd: float | None = None
    num_turns: int = 0
    duration_ms: int | None = None
    model: str | None = None
    api_key_source: str | None = None
    error: str | None = None
    error_kind: str | None = None
    turns: int = 0


class SessionIO(Protocol):
    """Channel between a running adapter and the orchestrator."""

    inbox: asyncio.Queue[str]
    stop_event: asyncio.Event

    async def emit(self, event: AgentEvent) -> None: ...

    async def request_permission(self, request: PermissionRequest) -> PermissionDecision: ...

    def set_pid(self, pid: int | None) -> None: ...


class AgentAdapter(Protocol):
    name: str

    async def run(self, spec: SessionSpec, io: SessionIO) -> SessionOutcome: ...
