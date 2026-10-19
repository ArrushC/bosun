"""Claude Agent SDK adapter: message normalisation, billing guard and the turn loop.

The SDK client is replaced by a scripted stand-in so the adapter's own logic is
tested deterministically; the real Claude path is validated separately
(see docs/VALIDATION.md).
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest
from claude_agent_sdk import (
    AssistantMessage,
    RateLimitEvent,
    RateLimitInfo,
    ResultMessage,
    SystemMessage,
    TaskNotificationMessage,
    TaskStartedMessage,
    TextBlock,
    ToolResultBlock,
    ToolUseBlock,
    UserMessage,
)
from claude_agent_sdk._errors import CLINotFoundError
from claude_agent_sdk.types import ToolPermissionContext

from bosun.agents import claude_sdk
from bosun.agents.claude_sdk import ClaudeAgentAdapter
from bosun.agents.contracts import (
    AgentEvent,
    ErrorKind,
    PermissionDecision,
    PermissionRequest,
    ProviderRuntime,
    SessionSpec,
)
from bosun.domain.enums import Provider, SessionStatus

SUBSCRIPTION = ProviderRuntime(
    provider=Provider.SUBSCRIPTION,
    label="Claude subscription",
    env={},
    expected_api_key_sources=frozenset({"none"}),
    expected_usage_providers=frozenset({"firstParty"}),
)


def spec(tmp_path: Path, **kw: Any) -> SessionSpec:
    return SessionSpec(
        session_id="ses_1",
        run_id="run_1",
        task_id="tsk_1",
        role="implementer",
        prompt="do it",
        system_prompt_append="",
        cwd=tmp_path,
        allowed_roots=[tmp_path],
        tools=["Read"],
        auto_allow=["Read"],
        disallowed_tools=[],
        provider=kw.pop("provider", SUBSCRIPTION),
        timeout_s=kw.pop("timeout_s", 10),
        **kw,
    )


class IO:
    def __init__(self) -> None:
        self.inbox: asyncio.Queue[str] = asyncio.Queue()
        self.stop_event = asyncio.Event()
        self.events: list[AgentEvent] = []

    async def emit(self, event: AgentEvent) -> None:
        self.events.append(event)

    async def request_permission(self, request: PermissionRequest) -> PermissionDecision:
        return PermissionDecision(allow=True)

    def set_pid(self, pid: int | None) -> None:
        pass

    def types(self) -> list[str]:
        return [e.type for e in self.events]


def init(api_key_source: str = "none") -> SystemMessage:
    return SystemMessage(
        subtype="init",
        data={
            "session_id": "sdk-1",
            "apiKeySource": api_key_source,
            "model": "claude-opus-5-5",
            "tools": ["Read"],
        },
    )


def result(
    text: str = "done",
    *,
    is_error: bool = False,
    subtype: str = "success",
    providers: tuple[str, ...] = ("firstParty",),
    **kw: Any,
) -> ResultMessage:
    return ResultMessage(
        subtype=subtype,
        duration_ms=100,
        duration_api_ms=90,
        is_error=is_error,
        num_turns=1,
        session_id="sdk-1",
        result=text,
        usage={"input_tokens": 10, "output_tokens": 5},
        model_usage={f"m{i}": {"provider": p} for i, p in enumerate(providers)},  # type: ignore[misc]
        **kw,
    )


def assistant(*blocks: Any, parent: str | None = None) -> AssistantMessage:
    return AssistantMessage(
        content=list(blocks), model="claude-opus-5-5", parent_tool_use_id=parent, session_id="sdk-1"
    )


EOF = object()


class ScriptedClient:
    """Stand-in for ClaudeSDKClient: each query() releases the next scripted turn."""

    script: dict[str, Any] = {}
    instances: list[ScriptedClient] = []

    def __init__(self, options: Any) -> None:
        self.options = options
        self.queries: list[str] = []
        self.interrupted = False
        self.disconnected = False
        self._queue: asyncio.Queue[Any] = asyncio.Queue()
        ScriptedClient.instances.append(self)

    async def connect(self) -> None:
        if error := self.script.get("connect_error"):
            raise error

    async def query(self, prompt: str) -> None:
        self.queries.append(prompt)
        turns = self.script.get("turns", [])
        index = len(self.queries) - 1
        if index < len(turns):
            asyncio.get_running_loop().create_task(self._play(turns[index]))

    async def _play(self, items: list[Any]) -> None:
        for item in items:
            if isinstance(item, float):
                await asyncio.sleep(item)
            elif callable(item):
                await item(self)
            else:
                await self._queue.put(item)

    async def receive_messages(self):  # noqa: ANN201
        while True:
            item = await self._queue.get()
            if item is EOF:
                return
            yield item

    async def interrupt(self) -> None:
        self.interrupted = True
        await self._queue.put(
            result(
                "interrupted",
                is_error=True,
                subtype="error_during_execution",
                terminal_reason="aborted_streaming",
            )
        )

    async def disconnect(self) -> None:
        self.disconnected = True


@pytest.fixture(autouse=True)
def scripted(monkeypatch: pytest.MonkeyPatch):  # noqa: ANN201
    ScriptedClient.instances = []
    monkeypatch.setattr(claude_sdk, "ClaudeSDKClient", ScriptedClient)
    yield ScriptedClient


async def test_normal_turn_maps_messages_to_events(
    tmp_path: Path, scripted: type[ScriptedClient]
) -> None:
    scripted.script = {
        "turns": [
            [
                init(),
                assistant(
                    TextBlock("Looking"),
                    ToolUseBlock(id="t1", name="Read", input={"file_path": "a.py"}),
                ),
                UserMessage(
                    content=[
                        ToolResultBlock(
                            tool_use_id="t1", content=[{"type": "text", "text": "print(1)"}]
                        )
                    ]
                ),
                result("All done"),
            ]
        ]
    }
    io = IO()
    outcome = await ClaudeAgentAdapter().run(spec(tmp_path), io)
    assert outcome.status == SessionStatus.COMPLETED
    assert outcome.sdk_session_id == "sdk-1" and outcome.result == "All done"
    assert outcome.api_key_source == "none"
    assert io.types() == [
        "session.init",
        "assistant.text",
        "tool.call",
        "tool.result",
        "session.turn_completed",
    ]
    tool_result = io.events[3]
    assert tool_result.data["output"] == "print(1)" and tool_result.data["name"] == "Read"
    assert scripted.instances[0].disconnected


async def test_subagent_delegation_is_observable(
    tmp_path: Path, scripted: type[ScriptedClient]
) -> None:
    scripted.script = {
        "turns": [
            [
                init(),
                assistant(
                    ToolUseBlock(
                        id="agent1",
                        name="Agent",
                        input={
                            "subagent_type": "researcher",
                            "description": "Map code",
                            "prompt": "Find X",
                        },
                    )
                ),
                TaskStartedMessage(
                    subtype="task_started",
                    data={},
                    task_id="task1",
                    description="Map code",
                    uuid="u",
                    session_id="sdk-1",
                    tool_use_id="agent1",
                ),
                assistant(
                    ToolUseBlock(id="t2", name="Grep", input={"pattern": "X"}), parent="agent1"
                ),
                UserMessage(
                    content=[ToolResultBlock(tool_use_id="t2", content="3 matches")],
                    parent_tool_use_id="agent1",
                ),
                UserMessage(
                    content=[ToolResultBlock(tool_use_id="agent1", content="Found X in a.py")]
                ),
                TaskNotificationMessage(
                    subtype="task_notification",
                    data={},
                    task_id="task1",
                    status="completed",
                    output_file="",
                    summary="ok",
                    uuid="u2",
                    session_id="sdk-1",
                    tool_use_id="agent1",
                ),
                result(),
            ]
        ]
    }
    io = IO()
    outcome = await ClaudeAgentAdapter().run(spec(tmp_path), io)
    assert outcome.status == SessionStatus.COMPLETED
    spawned = next(e for e in io.events if e.type == "subagent.spawned")
    assert spawned.data["subagent_type"] == "researcher" and spawned.data["tool_use_id"] == "agent1"
    inner = [e for e in io.events if e.type == "tool.call" and e.data["name"] == "Grep"]
    assert inner[0].data["parent_tool_use_id"] == "agent1"
    assert any(e.type == "subagent.returned" and "Found X" in e.data["output"] for e in io.events)
    assert [e.type for e in io.events if e.type.startswith("task.")] == [
        "task.started",
        "task.finished",
    ]


async def test_billing_guard_on_unexpected_api_key(
    tmp_path: Path, scripted: type[ScriptedClient]
) -> None:
    scripted.script = {"turns": [[init("ANTHROPIC_API_KEY"), 0.5, result()]]}
    io = IO()
    outcome = await ClaudeAgentAdapter().run(spec(tmp_path), io)
    assert outcome.status == SessionStatus.FAILED
    assert outcome.error_kind == ErrorKind.BILLING_GUARD
    assert "ANTHROPIC_API_KEY" in (outcome.error or "")
    assert scripted.instances[0].interrupted


async def test_billing_guard_on_unexpected_usage_provider(
    tmp_path: Path, scripted: type[ScriptedClient]
) -> None:
    scripted.script = {"turns": [[init(), result(providers=("vertex",))]]}
    outcome = await ClaudeAgentAdapter().run(spec(tmp_path), IO())
    assert outcome.error_kind == ErrorKind.BILLING_GUARD


async def test_usage_limit_rejection(tmp_path: Path, scripted: type[ScriptedClient]) -> None:
    scripted.script = {
        "turns": [
            [
                init(),
                RateLimitEvent(
                    rate_limit_info=RateLimitInfo(
                        status="rejected", resets_at=2_000_000_000, rate_limit_type="five_hour"
                    ),
                    uuid="u",
                    session_id="sdk-1",
                ),
                result("limit", is_error=True, subtype="success", api_error_status=429),
            ]
        ]
    }
    io = IO()
    outcome = await ClaudeAgentAdapter().run(spec(tmp_path), io)
    assert outcome.status == SessionStatus.FAILED
    assert outcome.error_kind == ErrorKind.USAGE_LIMIT
    assert any(e.type == "rate_limit" and e.status == "rejected" for e in io.events)


async def test_max_turns(tmp_path: Path, scripted: type[ScriptedClient]) -> None:
    scripted.script = {"turns": [[init(), result("", is_error=True, subtype="error_max_turns")]]}
    outcome = await ClaudeAgentAdapter().run(spec(tmp_path, max_turns=3), IO())
    assert outcome.error_kind == ErrorKind.MAX_TURNS


async def test_follow_up_is_delivered_after_the_current_turn(
    tmp_path: Path, scripted: type[ScriptedClient]
) -> None:
    scripted.script = {"turns": [[init(), 0.2, result("first")], [result("second")]]}
    io = IO()
    adapter = ClaudeAgentAdapter()
    task = asyncio.create_task(adapter.run(spec(tmp_path), io))
    await asyncio.sleep(0.05)
    await io.inbox.put("also do Y")
    outcome = await task
    client = scripted.instances[0]
    assert client.queries == ["do it", "also do Y"]
    assert (
        outcome.status == SessionStatus.COMPLETED
        and outcome.result == "second"
        and outcome.turns == 2
    )
    messages = [e for e in io.events if e.type == "user.message"]
    assert messages[0].data.get("queued") and messages[1].data.get("delivered")


async def test_stop_interrupts_the_turn(tmp_path: Path, scripted: type[ScriptedClient]) -> None:
    scripted.script = {"turns": [[init(), 5.0, result("never")]]}
    io = IO()
    task = asyncio.create_task(ClaudeAgentAdapter().run(spec(tmp_path), io))
    await asyncio.sleep(0.05)
    io.stop_event.set()
    outcome = await asyncio.wait_for(task, 3)
    assert outcome.status == SessionStatus.INTERRUPTED
    assert scripted.instances[0].interrupted


async def test_waits_for_background_tasks_and_injected_turn(
    tmp_path: Path, scripted: type[ScriptedClient]
) -> None:
    scripted.script = {
        "turns": [
            [
                init(),
                TaskStartedMessage(
                    subtype="task_started",
                    data={},
                    task_id="bg",
                    description="bg",
                    uuid="u",
                    session_id="sdk-1",
                    tool_use_id="a1",
                ),
                result("main turn done"),
                0.1,
                TaskNotificationMessage(
                    subtype="task_notification",
                    data={},
                    task_id="bg",
                    status="completed",
                    output_file="",
                    summary="bg ok",
                    uuid="u2",
                    session_id="sdk-1",
                    tool_use_id="a1",
                ),
                UserMessage(
                    content="<task-notification>bg done</task-notification>",
                    origin={"kind": "task-notification"},
                ),
                assistant(TextBlock("Background work finished")),
                result("final"),
            ]
        ]
    }
    io = IO()
    outcome = await asyncio.wait_for(ClaudeAgentAdapter().run(spec(tmp_path), io), 5)
    assert outcome.result == "final"
    assert "session.injected_turn" in io.types()


async def test_process_exit_without_result_is_a_crash(
    tmp_path: Path, scripted: type[ScriptedClient]
) -> None:
    scripted.script = {"turns": [[init(), EOF]]}
    outcome = await ClaudeAgentAdapter().run(spec(tmp_path), IO())
    assert outcome.status == SessionStatus.FAILED and outcome.error_kind == ErrorKind.PROCESS_CRASH


async def test_missing_cli(tmp_path: Path, scripted: type[ScriptedClient]) -> None:
    scripted.script = {"connect_error": CLINotFoundError("claude not found")}
    outcome = await ClaudeAgentAdapter().run(spec(tmp_path), IO())
    assert outcome.error_kind == ErrorKind.CLI_MISSING


async def test_session_timeout(tmp_path: Path, scripted: type[ScriptedClient]) -> None:
    scripted.script = {"turns": [[init(), 5.0, result()]]}
    outcome = await ClaudeAgentAdapter().run(spec(tmp_path, timeout_s=0.2), IO())
    assert outcome.status == SessionStatus.FAILED and outcome.error_kind == ErrorKind.TIMEOUT


async def test_options_pin_provider_and_restrict_tools(
    tmp_path: Path, scripted: type[ScriptedClient]
) -> None:
    scripted.script = {"turns": [[init(), result()]]}
    runtime = ProviderRuntime(
        provider=Provider.SUBSCRIPTION,
        label="Claude subscription",
        env={"ANTHROPIC_API_KEY": ""},
        expected_api_key_sources=frozenset({"none"}),
        expected_usage_providers=frozenset({"firstParty"}),
    )
    await ClaudeAgentAdapter().run(spec(tmp_path, provider=runtime, setting_sources=[]), IO())
    options = scripted.instances[0].options
    assert options.env["ANTHROPIC_API_KEY"] == ""  # inherited key blanked in subscription mode
    assert options.permission_mode == "default"
    assert options.tools == ["Read"] and options.allowed_tools == ["Read"]
    assert options.setting_sources == [] and options.strict_mcp_config is True
    assert (
        options.verbatim_prompts is True
    )  # imported text cannot trigger @file reads or slash commands
    assert options.can_use_tool is not None


class SlowApprovalIO(IO):
    async def request_permission(self, request: PermissionRequest) -> PermissionDecision:
        await asyncio.sleep(0.6)  # a human taking longer than the whole session budget
        return PermissionDecision(allow=True)


async def test_time_waiting_for_a_human_does_not_count_against_the_timeout(
    tmp_path: Path, scripted: type[ScriptedClient]
) -> None:
    async def ask(client: ScriptedClient) -> None:
        decision = await client.options.can_use_tool(
            "Bash", {"command": "make test"}, ToolPermissionContext(tool_use_id="t1")
        )
        assert decision.behavior == "allow"

    scripted.script = {"turns": [[init(), ask, result("finished after approval")]]}
    outcome = await asyncio.wait_for(
        ClaudeAgentAdapter().run(spec(tmp_path, timeout_s=0.3), SlowApprovalIO()), 5
    )
    assert outcome.status == SessionStatus.COMPLETED, outcome.error
    assert outcome.result == "finished after approval"


async def test_unfinished_background_task_keeps_the_completed_result(
    tmp_path: Path, scripted: type[ScriptedClient]
) -> None:
    scripted.script = {
        "turns": [
            [
                init(),
                TaskStartedMessage(
                    subtype="task_started",
                    data={},
                    task_id="watcher",
                    description="npm run dev",
                    uuid="u",
                    session_id="sdk-1",
                    tool_use_id="a1",
                ),
                result("main turn done"),
            ]
        ]
    }
    io = IO()
    outcome = await asyncio.wait_for(ClaudeAgentAdapter().run(spec(tmp_path, timeout_s=0.3), io), 5)
    assert outcome.status == SessionStatus.COMPLETED and outcome.result == "main turn done"
    assert "session.notice" in io.types()


async def test_billing_guard_requires_an_auth_source(
    tmp_path: Path, scripted: type[ScriptedClient]
) -> None:
    missing = SystemMessage(subtype="init", data={"session_id": "sdk-1", "model": "m", "tools": []})
    scripted.script = {"turns": [[missing, result()]]}
    outcome = await ClaudeAgentAdapter().run(spec(tmp_path), IO())
    assert outcome.status == SessionStatus.FAILED
    assert outcome.error_kind == ErrorKind.BILLING_GUARD


async def test_messages_that_never_reached_claude_return_to_the_inbox(
    tmp_path: Path, scripted: type[ScriptedClient]
) -> None:
    io = IO()

    async def late_message(_client: ScriptedClient) -> None:
        await io.inbox.put("one more thing")

    # The message arrives while the result is already on its way; the adapter
    # stops without sending it, and must hand it back rather than drop it.
    scripted.script = {"turns": [[init(), result("done"), late_message]]}
    io.stop_event.set()
    await ClaudeAgentAdapter().run(spec(tmp_path), io)
    assert io.inbox.qsize() == 1 and io.inbox.get_nowait() == "one more thing"


async def test_sandbox_fails_closed_when_unavailable(
    tmp_path: Path, scripted: type[ScriptedClient]
) -> None:
    from claude_agent_sdk._errors import ProcessError

    scripted.script = {
        "connect_error": ProcessError(
            "Claude Code returned an error result: Sandbox required but unavailable: "
            "socat not installed",
            exit_code=1,
        )
    }
    outcome = await ClaudeAgentAdapter().run(spec(tmp_path, sandbox_bash=True), IO())
    assert outcome.status == SessionStatus.FAILED
    assert outcome.error_kind == ErrorKind.SANDBOX_UNAVAILABLE
    sandbox = ScriptedClient.instances[-1].options.sandbox
    assert sandbox["enabled"] and sandbox["failIfUnavailable"]
    assert sandbox["allowUnsandboxedCommands"] is False
    assert sandbox["autoAllowBashIfSandboxed"] is False
