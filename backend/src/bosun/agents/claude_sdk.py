"""Claude Code execution through the Claude Agent SDK (`claude-agent-sdk`).

One `ClaudeSDKClient` (one Claude Code subprocess) per session. The adapter:

* streams SDK messages and normalises them into `AgentEvent`s (assistant text,
  tool calls/results, subagent delegation via the Agent tool and
  `parent_tool_use_id`, background task lifecycle, rate-limit state, results);
* routes permission prompts (`can_use_tool`) to Bosun's approval queue;
* enforces the workspace boundary with a `PreToolUse` hook;
* verifies the provider actually used (billing guard) from the init frame and
  per-model usage, aborting immediately on a mismatch;
* delivers follow-up instructions between turns and supports interrupting;
* keeps the session alive while background subagents/tasks are still running.

Thinking blocks are never recorded: hidden reasoning is not exposed.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import re
import time
import warnings
from collections import deque
from dataclasses import dataclass, field
from typing import Any

from claude_agent_sdk import (
    TERMINAL_TASK_STATUSES as SDK_TERMINAL_TASK_STATUSES,
)
from claude_agent_sdk import (
    AgentDefinition,
    AssistantMessage,
    CanUseToolShadowedWarning,
    ClaudeAgentOptions,
    ClaudeSDKClient,
    HookMatcher,
    PermissionResultAllow,
    PermissionResultDeny,
    RateLimitEvent,
    ResultMessage,
    ServerToolResultBlock,
    ServerToolUseBlock,
    SystemMessage,
    TaskNotificationMessage,
    TaskProgressMessage,
    TaskStartedMessage,
    TaskUpdatedMessage,
    TextBlock,
    ToolPermissionContext,
    ToolResultBlock,
    ToolUseBlock,
    UserMessage,
)
from claude_agent_sdk._errors import (
    ClaudeSDKError,
    CLIConnectionError,
    CLINotFoundError,
    ProcessError,
    ResultError,
)

from bosun.agents.contracts import (
    AgentEvent,
    ErrorKind,
    PermissionRequest,
    SessionIO,
    SessionOutcome,
    SessionSpec,
)
from bosun.agents.permissions import boundary_violation
from bosun.domain.enums import SessionStatus

log = logging.getLogger(__name__)

# Whole-tool `allowed_tools` entries intentionally bypass `can_use_tool`; only
# calls outside the allow list should reach the approval queue.
warnings.filterwarnings("ignore", category=CanUseToolShadowedWarning)

SUBAGENT_TOOL_NAMES = {"Agent", "Task"}
MAX_TOOL_OUTPUT = 4_000
MAX_TEXT = 8_000
# How long to wait for injected turns once background tasks finish.
BACKGROUND_IDLE_GRACE_S = 120.0
STOP_GRACE_S = 20.0
# While a human decides on a permission request the session clock is paused;
# the consumer re-checks at this interval.
HUMAN_WAIT_POLL_S = 30.0


class BillingGuardViolation(Exception):
    pass


def _block_text(content: Any) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, dict):
                if item.get("type") == "text":
                    parts.append(str(item.get("text", "")))
                elif item.get("type") == "image":
                    parts.append("[image]")
                else:
                    parts.append(str(item.get("text") or item.get("type") or ""))
            else:
                parts.append(str(item))
        return "\n".join(p for p in parts if p)
    return str(content)


def _truncate(text: str, limit: int) -> str:
    return text if len(text) <= limit else f"{text[:limit]}… [{len(text) - limit} more chars]"


def classify_assistant_error(error: str | None) -> str | None:
    return {
        "authentication_failed": ErrorKind.AUTH_REQUIRED,
        "billing_error": ErrorKind.BILLING,
        "rate_limit": ErrorKind.RATE_LIMITED,
        "invalid_request": ErrorKind.INVALID_REQUEST,
        "server_error": ErrorKind.SERVER_ERROR,
        "unknown": ErrorKind.UNKNOWN,
    }.get(error or "")


_ERROR_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (
        re.compile(r"\bsandbox required but unavailable\b", re.IGNORECASE),
        ErrorKind.SANDBOX_UNAVAILABLE,
    ),
    (
        re.compile(
            r"\bnot logged in\b|please run /login|\binvalid api key\b|"
            r"\bauthentication (?:failed|error|required)\b|\b401\b(?! *(?:lines?|files?|tests?))",
            re.IGNORECASE,
        ),
        ErrorKind.AUTH_REQUIRED,
    ),
    (
        re.compile(r"\busage limit\b|\blimit reached\b|\bout of extra usage\b", re.IGNORECASE),
        ErrorKind.USAGE_LIMIT,
    ),
    (
        re.compile(r"\bcredit balance\b|\bbilling (?:error|issue|required)\b", re.IGNORECASE),
        ErrorKind.BILLING,
    ),
    (
        re.compile(
            r"\b429\b(?! *(?:lines?|files?|tests?))|\brate[ -]limit(?:ed)?\b|\boverloaded(?:_error)?\b",
            re.IGNORECASE,
        ),
        ErrorKind.RATE_LIMITED,
    ),
]


def classify_text(text: str) -> str | None:
    for pattern, kind in _ERROR_PATTERNS:
        if pattern.search(text):
            return kind
    return None


@dataclass
class _State:
    started: float = field(default_factory=time.monotonic)
    deadline: float = 0.0
    sdk_session_id: str | None = None
    model: str | None = None
    api_key_source: str | None = None
    awaiting_result: bool = False
    stopping: bool = False
    stop_deadline: float | None = None
    active_tasks: set[str] = field(default_factory=set)
    idle_deadline: float | None = None
    pending_instructions: deque[str] = field(default_factory=deque)
    tool_started: dict[str, float] = field(default_factory=dict)
    tool_names: dict[str, str] = field(default_factory=dict)
    last_result: ResultMessage | None = None
    turns: int = 0
    num_turns: int = 0
    usage: dict[str, int] = field(default_factory=dict)
    cost_usd: float | None = None
    duration_ms: int = 0
    error: str | None = None
    error_kind: str | None = None
    structured_output: Any = None
    result_text: str | None = None
    init_emitted: bool = False
    rate_limit_signature: tuple[Any, ...] | None = None
    human_waits: int = 0
    human_wait_started: float = 0.0


class ClaudeAgentAdapter:
    name = "claude"

    def __init__(self, cli_path: str | None = None) -> None:
        self._cli_path = cli_path

    # -- options ---------------------------------------------------------------
    def _options(
        self, spec: SessionSpec, io: SessionIO, state: _State, stderr_tail: deque[str]
    ) -> ClaudeAgentOptions:
        async def can_use_tool(
            tool_name: str, tool_input: dict[str, Any], ctx: ToolPermissionContext
        ):
            violation = boundary_violation(
                tool_name, tool_input, spec.cwd, spec.allowed_roots, spec.protected_roots
            )
            if violation:
                await io.emit(
                    AgentEvent(
                        "tool.denied",
                        {"tool_use_id": ctx.tool_use_id, "tool": tool_name, "reason": violation},
                    )
                )
                return PermissionResultDeny(message=violation)
            # Time spent waiting for a human does not count against the session
            # timeout. Overlapping requests (parallel subagents) pause the clock once.
            if state.human_waits == 0:
                state.human_wait_started = time.monotonic()
            state.human_waits += 1
            try:
                decision = await io.request_permission(
                    PermissionRequest(
                        tool_name=tool_name,
                        tool_input=tool_input,
                        tool_use_id=ctx.tool_use_id,
                        title=ctx.title,
                        description=ctx.description,
                        decision_reason=ctx.decision_reason,
                        blocked_path=ctx.blocked_path,
                        subagent_id=ctx.agent_id,
                    )
                )
            finally:
                state.human_waits -= 1
                if state.human_waits == 0:
                    state.deadline += time.monotonic() - state.human_wait_started
            if decision.allow:
                return PermissionResultAllow(updated_input=decision.updated_input)
            return PermissionResultDeny(
                message=decision.message or "Denied by the operator", interrupt=decision.interrupt
            )

        async def boundary_hook(input_data: Any, tool_use_id: str | None, _ctx: Any) -> Any:
            tool_name = input_data.get("tool_name", "")
            tool_input = input_data.get("tool_input") or {}
            violation = boundary_violation(
                tool_name, tool_input, spec.cwd, spec.allowed_roots, spec.protected_roots
            )
            if not violation:
                return {}
            await io.emit(
                AgentEvent(
                    "tool.denied",
                    {"tool_use_id": tool_use_id, "tool": tool_name, "reason": violation},
                )
            )
            return {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "deny",
                    "permissionDecisionReason": violation,
                }
            }

        agents = None
        if spec.subagents:
            agents = {
                name: AgentDefinition(
                    description=s.description, prompt=s.prompt, tools=s.tools, model=s.model
                )
                for name, s in spec.subagents.items()
            }

        def on_stderr(line: str) -> None:
            stderr_tail.append(line.rstrip())

        return ClaudeAgentOptions(
            tools=spec.tools,
            allowed_tools=spec.auto_allow,
            disallowed_tools=spec.disallowed_tools,
            system_prompt={
                "type": "preset",
                "preset": "claude_code",
                "append": spec.system_prompt_append,
            },
            permission_mode="default",
            cwd=str(spec.cwd),
            model=spec.model,
            effort=spec.effort,  # type: ignore[arg-type]
            max_turns=spec.max_turns,
            resume=spec.resume_sdk_session_id,
            can_use_tool=can_use_tool,
            hooks={"PreToolUse": [HookMatcher(matcher=None, hooks=[boundary_hook])]},  # type: ignore[list-item]
            env={
                **spec.provider.env,
                "CLAUDE_CODE_MAX_SUBAGENT_SPAWN_DEPTH": "1",
                "CLAUDE_CODE_MAX_CONCURRENT_SUBAGENTS": "4",
            },
            setting_sources=spec.setting_sources,  # type: ignore[arg-type]
            strict_mcp_config=True,
            agents=agents,
            output_format={"type": "json_schema", "schema": spec.output_schema}
            if spec.output_schema
            else None,
            verbatim_prompts=True,
            forward_subagent_text=True,
            cli_path=self._cli_path,
            stderr=on_stderr,
            # Commands still go through the approval queue; the sandbox only adds an
            # OS-level filesystem/network boundary underneath.
            sandbox={  # type: ignore[typeddict-unknown-key]
                "enabled": True,
                "autoAllowBashIfSandboxed": False,
                "allowUnsandboxedCommands": False,
                # Refuse to start rather than silently run commands unconfined when
                # the sandbox backend (bubblewrap + socat on Linux) is missing.
                "failIfUnavailable": True,
            }
            if spec.sandbox_bash
            else None,
        )

    # -- main loop ---------------------------------------------------------------
    async def run(self, spec: SessionSpec, io: SessionIO) -> SessionOutcome:
        state = _State()
        state.deadline = time.monotonic() + spec.timeout_s
        stderr_tail: deque[str] = deque(maxlen=40)
        client = ClaudeSDKClient(self._options(spec, io, state, stderr_tail))
        queue: asyncio.Queue[tuple[str, Any]] = asyncio.Queue()
        helpers: list[asyncio.Task[None]] = []

        try:
            await client.connect()
        except CLINotFoundError as exc:
            return self._fail(state, ErrorKind.CLI_MISSING, f"Claude Code CLI not found: {exc}")
        except (CLIConnectionError, ProcessError, ClaudeSDKError, OSError) as exc:
            text = f"{exc}\n" + "\n".join(stderr_tail)
            return self._fail(
                state,
                classify_text(text) or ErrorKind.PROCESS_CRASH,
                f"Could not start Claude Code: {exc}",
            )

        try:
            transport = getattr(client, "_transport", None)
            process = getattr(transport, "_process", None)
            io.set_pid(getattr(process, "pid", None))

            helpers.append(asyncio.create_task(self._reader(client, queue)))
            helpers.append(asyncio.create_task(self._inbox_pump(io, queue)))
            helpers.append(asyncio.create_task(self._stop_watch(io, queue)))

            await client.query(spec.prompt)
            state.awaiting_result = True
            state.turns = 1
            await self._consume(client, spec, io, state, queue)
        except BillingGuardViolation as exc:
            state.error_kind, state.error = ErrorKind.BILLING_GUARD, str(exc)
            with contextlib.suppress(Exception):
                await asyncio.wait_for(client.interrupt(), 5)
        except TimeoutError:
            state.error_kind = ErrorKind.TIMEOUT
            state.error = f"Session exceeded its time limit of {int(spec.timeout_s // 60)} minutes"
            with contextlib.suppress(Exception):
                await asyncio.wait_for(client.interrupt(), 5)
        except ResultError as exc:
            state.error_kind = state.error_kind or classify_text(str(exc)) or ErrorKind.UNKNOWN
            state.error = state.error or str(exc)
        except (ProcessError, ClaudeSDKError) as exc:
            text = f"{exc}\n" + "\n".join(stderr_tail)
            state.error_kind = state.error_kind or classify_text(text) or ErrorKind.PROCESS_CRASH
            state.error = state.error or f"Claude Code process error: {exc}"
        finally:
            for task in helpers:
                task.cancel()
            for task in helpers:
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await task
            self._return_undelivered(io, state, queue)
            with contextlib.suppress(Exception):
                await asyncio.wait_for(client.disconnect(), 15)
            io.set_pid(None)
        return self._outcome(state)

    @staticmethod
    def _return_undelivered(
        io: SessionIO, state: _State, queue: asyncio.Queue[tuple[str, Any]]
    ) -> None:
        """Hand operator messages that never reached Claude back to the session inbox."""
        leftovers = list(state.pending_instructions)
        state.pending_instructions.clear()
        while not queue.empty():
            kind, payload = queue.get_nowait()
            if kind == "instruction":
                leftovers.append(payload)
        pending = []
        while not io.inbox.empty():
            pending.append(io.inbox.get_nowait())
        for text in [*leftovers, *pending]:
            io.inbox.put_nowait(text)

    async def _reader(self, client: ClaudeSDKClient, queue: asyncio.Queue[tuple[str, Any]]) -> None:
        try:
            async for message in client.receive_messages():
                await queue.put(("message", message))
        except Exception as exc:  # noqa: BLE001 - surfaced to the consumer
            await queue.put(("error", exc))
            return
        await queue.put(("eof", None))

    async def _inbox_pump(self, io: SessionIO, queue: asyncio.Queue[tuple[str, Any]]) -> None:
        while True:
            text = await io.inbox.get()
            await queue.put(("instruction", text))

    async def _stop_watch(self, io: SessionIO, queue: asyncio.Queue[tuple[str, Any]]) -> None:
        await io.stop_event.wait()
        await queue.put(("stop", None))

    async def _consume(
        self,
        client: ClaudeSDKClient,
        spec: SessionSpec,
        io: SessionIO,
        state: _State,
        queue: asyncio.Queue[tuple[str, Any]],
    ) -> None:
        while True:
            turn_open = state.awaiting_result or bool(state.active_tasks)
            if not turn_open:
                if state.stopping:
                    return
                if state.pending_instructions:
                    text = state.pending_instructions.popleft()
                    await io.emit(AgentEvent("user.message", {"text": text, "delivered": True}))
                    await client.query(text)
                    state.awaiting_result = True
                    state.turns += 1
                    continue
                if state.idle_deadline is None:
                    return

            now = time.monotonic()
            deadlines = [
                max(state.deadline, now + HUMAN_WAIT_POLL_S)
                if state.human_waits
                else state.deadline
            ]
            if state.stop_deadline is not None:
                deadlines.append(state.stop_deadline)
            if state.idle_deadline is not None and not state.awaiting_result:
                deadlines.append(state.idle_deadline)
            timeout = max(0.0, min(deadlines) - now)
            try:
                kind, payload = await asyncio.wait_for(queue.get(), timeout)
            except TimeoutError:
                if state.stop_deadline is not None and time.monotonic() >= state.stop_deadline:
                    return  # interrupt did not settle; disconnect will terminate the process
                if state.idle_deadline is not None and time.monotonic() >= state.idle_deadline:
                    state.idle_deadline = None
                    state.active_tasks.clear()
                    continue
                if state.human_waits or time.monotonic() < state.deadline:
                    continue  # paused for an approval, or the deadline was extended
                if (
                    not state.awaiting_result
                    and state.last_result is not None
                    and state.error is None
                ):
                    # The agent finished its turn; only background tasks were still
                    # running. Keep the completed result and stop the leftovers.
                    await io.emit(
                        AgentEvent(
                            "session.notice",
                            {
                                "text": "Background tasks were still running at the session time "
                                "limit and were stopped. The agent's last result is kept.",
                                "tasks": sorted(state.active_tasks),
                            },
                        )
                    )
                    state.active_tasks.clear()
                    with contextlib.suppress(Exception):
                        await asyncio.wait_for(client.interrupt(), 10)
                    return
                raise

            if kind == "message":
                await self._handle(payload, spec, io, state)
            elif kind == "instruction":
                if state.awaiting_result:
                    state.pending_instructions.append(payload)
                    await io.emit(AgentEvent("user.message", {"text": payload, "queued": True}))
                else:
                    state.pending_instructions.append(payload)
            elif kind == "stop":
                state.stopping = True
                if state.awaiting_result or state.active_tasks:
                    state.stop_deadline = time.monotonic() + STOP_GRACE_S
                    with contextlib.suppress(Exception):
                        await asyncio.wait_for(client.interrupt(), 10)
                else:
                    return
            elif kind == "error":
                raise payload
            elif kind == "eof":
                if state.awaiting_result and state.error is None:
                    state.error_kind = ErrorKind.PROCESS_CRASH
                    state.error = "Claude Code exited before finishing the turn"
                return

            if state.stopping and not state.awaiting_result:
                return

    # -- message normalisation ----------------------------------------------------
    async def _handle(self, msg: Any, spec: SessionSpec, io: SessionIO, state: _State) -> None:
        if isinstance(msg, TaskStartedMessage):
            state.active_tasks.add(msg.task_id)
            state.idle_deadline = None
            await io.emit(
                AgentEvent(
                    "task.started",
                    {
                        "task_id": msg.task_id,
                        "description": msg.description,
                        "tool_use_id": msg.tool_use_id,
                        "task_type": msg.task_type,
                    },
                    status="running",
                )
            )
        elif isinstance(msg, TaskProgressMessage):
            await io.emit(
                AgentEvent(
                    "task.progress",
                    {
                        "task_id": msg.task_id,
                        "tool_use_id": msg.tool_use_id,
                        "last_tool": msg.last_tool_name,
                        "usage": dict(msg.usage) if msg.usage else None,
                    },
                    status="running",
                )
            )
        elif isinstance(msg, TaskNotificationMessage):
            self._task_finished(state, msg.task_id)
            await io.emit(
                AgentEvent(
                    "task.finished",
                    {
                        "task_id": msg.task_id,
                        "tool_use_id": msg.tool_use_id,
                        "summary": _truncate(msg.summary or "", MAX_TEXT),
                        "usage": dict(msg.usage) if msg.usage else None,
                    },
                    status=msg.status,
                )
            )
        elif isinstance(msg, TaskUpdatedMessage):
            if msg.status in SDK_TERMINAL_TASK_STATUSES:
                self._task_finished(state, msg.task_id)
                await io.emit(
                    AgentEvent("task.finished", {"task_id": msg.task_id}, status=str(msg.status))
                )
        elif isinstance(msg, SystemMessage):
            if msg.subtype == "init":
                await self._on_init(msg.data, spec, io, state)
        elif isinstance(msg, AssistantMessage):
            await self._on_assistant(msg, io, state)
        elif isinstance(msg, UserMessage):
            await self._on_user(msg, io, state)
        elif isinstance(msg, ResultMessage):
            await self._on_result(msg, spec, io, state)
        elif isinstance(msg, RateLimitEvent):
            info = msg.rate_limit_info
            data = {
                "status": info.status,
                "type": info.rate_limit_type,
                "utilization": info.utilization,
                "resets_at": info.resets_at,
                "overage_status": info.overage_status,
            }
            signature = (info.status, info.rate_limit_type, info.overage_status, info.resets_at)
            if signature != state.rate_limit_signature:
                state.rate_limit_signature = signature
                await io.emit(AgentEvent("rate_limit", data, status=info.status))
            if info.status == "rejected":
                state.error_kind = ErrorKind.USAGE_LIMIT
                state.error = "Claude usage limit reached" + (
                    f"; resets at {time.strftime('%Y-%m-%d %H:%M', time.localtime(info.resets_at))}"
                    if info.resets_at
                    else ""
                )

    def _task_finished(self, state: _State, task_id: str) -> None:
        state.active_tasks.discard(task_id)
        if not state.active_tasks and not state.awaiting_result:
            # The CLI usually injects a turn to report finished background work.
            state.idle_deadline = time.monotonic() + BACKGROUND_IDLE_GRACE_S

    async def _on_init(
        self, data: dict[str, Any], spec: SessionSpec, io: SessionIO, state: _State
    ) -> None:
        state.sdk_session_id = data.get("session_id") or state.sdk_session_id
        state.model = data.get("model") or state.model
        state.api_key_source = data.get("apiKeySource")
        if not state.init_emitted:
            state.init_emitted = True
            await io.emit(
                AgentEvent(
                    "session.init",
                    {
                        "sdk_session_id": state.sdk_session_id,
                        "model": state.model,
                        "tools": data.get("tools", []),
                        "permission_mode": data.get("permissionMode"),
                        "api_key_source": state.api_key_source,
                        "claude_code_version": data.get("claude_code_version"),
                        "cwd": data.get("cwd"),
                        "agents": data.get("agents", []),
                    },
                )
            )
        expected = spec.provider.expected_api_key_sources
        if state.api_key_source is None:
            raise BillingGuardViolation(
                "Billing guard: Claude Code did not report how it authenticated, so Bosun "
                f"cannot confirm the session uses '{spec.provider.label}'. The session was stopped "
                "before doing work. Update Claude Code, or check the provider settings."
            )
        if state.api_key_source not in expected:
            raise BillingGuardViolation(
                f"Billing guard: Claude Code authenticated via '{state.api_key_source}' but the selected "
                f"provider is '{spec.provider.label}'. The session was stopped before doing work. "
                "Check for ANTHROPIC_API_KEY or provider settings in the workspace's .claude/settings.json."
            )

    async def _on_assistant(self, msg: AssistantMessage, io: SessionIO, state: _State) -> None:
        state.model = msg.model or state.model
        if not state.awaiting_result and msg.parent_tool_use_id is None:
            # A turn we did not start (e.g. injected after background work finished).
            state.awaiting_result = True
            state.idle_deadline = None
        if msg.session_id:
            state.sdk_session_id = msg.session_id
        parent = msg.parent_tool_use_id
        if msg.error:
            kind = classify_assistant_error(msg.error)
            if kind and kind != ErrorKind.UNKNOWN:
                state.error_kind = kind
        for block in msg.content:
            if isinstance(block, TextBlock):
                if block.text.strip():
                    await io.emit(
                        AgentEvent(
                            "assistant.text",
                            {"text": _truncate(block.text, MAX_TEXT), "parent_tool_use_id": parent},
                        )
                    )
            elif isinstance(block, ToolUseBlock | ServerToolUseBlock):
                state.tool_started[block.id] = time.monotonic()
                state.tool_names[block.id] = block.name
                await io.emit(
                    AgentEvent(
                        "tool.call",
                        {
                            "tool_use_id": block.id,
                            "name": block.name,
                            "input": block.input,
                            "parent_tool_use_id": parent,
                            "server_tool": isinstance(block, ServerToolUseBlock),
                        },
                        status="running",
                    )
                )
                if block.name in SUBAGENT_TOOL_NAMES:
                    await io.emit(
                        AgentEvent(
                            "subagent.spawned",
                            {
                                "tool_use_id": block.id,
                                "subagent_type": block.input.get("subagent_type")
                                or "general-purpose",
                                "description": block.input.get("description"),
                                "prompt": _truncate(str(block.input.get("prompt", "")), MAX_TEXT),
                                "background": block.input.get("run_in_background"),
                                "parent_tool_use_id": parent,
                            },
                            status="running",
                        )
                    )
            elif isinstance(block, ToolResultBlock | ServerToolResultBlock):
                await self._emit_tool_result(io, state, block, parent)

    async def _on_user(self, msg: UserMessage, io: SessionIO, state: _State) -> None:
        if isinstance(msg.content, str):
            origin = (msg.origin or {}).get("kind") if msg.origin else None
            if origin and origin != "human":
                await io.emit(
                    AgentEvent(
                        "session.injected_turn",
                        {"origin": origin, "text": _truncate(msg.content, 500)},
                    )
                )
                state.awaiting_result = True
                state.idle_deadline = None
            return
        for block in msg.content:
            if isinstance(block, ToolResultBlock | ServerToolResultBlock):
                await self._emit_tool_result(io, state, block, msg.parent_tool_use_id)

    async def _emit_tool_result(
        self, io: SessionIO, state: _State, block: Any, parent: str | None
    ) -> None:
        started = state.tool_started.pop(block.tool_use_id, None)
        name = state.tool_names.get(block.tool_use_id)
        is_error = bool(getattr(block, "is_error", False))
        content = block.content if isinstance(block, ToolResultBlock) else block.content
        text = (
            _block_text(content)
            if not isinstance(content, dict)
            else str(content.get("type", "result"))
        )
        await io.emit(
            AgentEvent(
                "tool.result",
                {
                    "tool_use_id": block.tool_use_id,
                    "name": name,
                    "is_error": is_error,
                    "output": _truncate(text, MAX_TOOL_OUTPUT),
                    "duration_ms": int((time.monotonic() - started) * 1000) if started else None,
                    "parent_tool_use_id": parent,
                },
                status="failed" if is_error else "completed",
            )
        )
        if name in SUBAGENT_TOOL_NAMES:
            await io.emit(
                AgentEvent(
                    "subagent.returned",
                    {"tool_use_id": block.tool_use_id, "output": _truncate(text, MAX_TEXT)},
                    status="failed" if is_error else "completed",
                )
            )

    async def _on_result(
        self, msg: ResultMessage, spec: SessionSpec, io: SessionIO, state: _State
    ) -> None:
        state.awaiting_result = False
        state.last_result = msg
        state.sdk_session_id = msg.session_id or state.sdk_session_id
        state.num_turns += msg.num_turns or 0
        state.duration_ms += msg.duration_ms or 0
        if msg.total_cost_usd is not None:
            state.cost_usd = msg.total_cost_usd
        for key, value in (msg.usage or {}).items():
            if isinstance(value, int):
                state.usage[key] = state.usage.get(key, 0) + value
        if msg.result is not None:
            state.result_text = msg.result
        if msg.structured_output is not None:
            state.structured_output = msg.structured_output
        if not state.active_tasks:
            state.idle_deadline = None

        providers = sorted(
            {
                str(u.get("provider"))
                for u in (msg.model_usage or {}).values()
                if isinstance(u, dict) and u.get("provider")
            }
        )
        await io.emit(
            AgentEvent(
                "session.turn_completed",
                {
                    "subtype": msg.subtype,
                    "is_error": msg.is_error,
                    "num_turns": msg.num_turns,
                    "duration_ms": msg.duration_ms,
                    "usage": msg.usage,
                    "total_cost_usd": msg.total_cost_usd,
                    "result": _truncate(msg.result or "", MAX_TEXT),
                    "terminal_reason": msg.terminal_reason,
                    "stop_reason": msg.stop_reason,
                    "permission_denials": len(msg.permission_denials or []),
                    "providers": providers,
                    "api_error_status": msg.api_error_status,
                    "origin": (msg.origin or {}).get("kind") if msg.origin else None,
                },
                status="failed" if msg.is_error else "completed",
            )
        )
        unexpected = [p for p in providers if p not in spec.provider.expected_usage_providers]
        if unexpected:
            raise BillingGuardViolation(
                f"Billing guard: usage was reported by provider(s) {unexpected}, but the selected provider is "
                f"'{spec.provider.label}'. The session was stopped."
            )

        if msg.is_error and not state.stopping:
            if msg.subtype == "error_max_turns" or msg.terminal_reason == "max_turns":
                state.error_kind = ErrorKind.MAX_TURNS
                state.error = f"Reached the maximum of {spec.max_turns} turns"
            else:
                text = "\n".join([msg.result or "", *(msg.errors or [])])
                state.error_kind = (
                    state.error_kind
                    or classify_text(text)
                    or (
                        ErrorKind.RATE_LIMITED if msg.api_error_status == 429 else ErrorKind.UNKNOWN
                    )
                )
                state.error = text.strip() or f"Claude Code reported an error ({msg.subtype})"
        elif not msg.is_error and not state.stopping:
            state.error = None
            state.error_kind = None

    # -- outcome ---------------------------------------------------------------------
    def _fail(self, state: _State, kind: str, error: str) -> SessionOutcome:
        state.error_kind, state.error = kind, error
        return self._outcome(state)

    def _outcome(self, state: _State) -> SessionOutcome:
        if state.error_kind == ErrorKind.BILLING_GUARD or (state.error and not state.stopping):
            status = SessionStatus.FAILED
        elif state.stopping:
            status = SessionStatus.INTERRUPTED
        elif state.last_result is None:
            status = SessionStatus.FAILED
            state.error = state.error or "Session ended without a result"
            state.error_kind = state.error_kind or ErrorKind.PROCESS_CRASH
        else:
            status = SessionStatus.COMPLETED
        return SessionOutcome(
            status=status,
            sdk_session_id=state.sdk_session_id,
            result=state.result_text,
            structured_output=state.structured_output,
            usage=state.usage,
            cost_usd=state.cost_usd,
            num_turns=state.num_turns,
            duration_ms=state.duration_ms or int((time.monotonic() - state.started) * 1000),
            model=state.model,
            api_key_source=state.api_key_source,
            error=state.error,
            error_kind=state.error_kind,
            turns=state.turns,
        )
