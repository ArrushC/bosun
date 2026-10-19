"""Deterministic fake agent backend for tests and demo mode.

Implements the same `AgentAdapter` contract as the Claude adapter and goes
through the same orchestration paths: it emits the same normalised events,
requests permissions through `SessionIO`, makes real (small, clearly labelled)
file edits inside its working directory so worktree commits, diffs and merges
are exercised, honours stop requests and follow-up instructions, and returns
structured plans for coordinator sessions.

It never contacts Claude and is always labelled "demo" in the UI.
"""

from __future__ import annotations

import asyncio
import re
import uuid
from pathlib import Path
from typing import Any

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


class _Stopped(Exception):
    pass


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:40] or "task"


class FakeAgentAdapter:
    name = "demo"

    def __init__(self, step_delay: float = 0.8) -> None:
        self.step_delay = step_delay

    async def run(self, spec: SessionSpec, io: SessionIO) -> SessionOutcome:
        scenario = spec.scenario
        sdk_session_id = spec.resume_sdk_session_id or str(uuid.uuid4())
        ctx = _Ctx(self, spec, io)
        try:
            await ctx.emit(
                "session.init",
                {
                    "sdk_session_id": sdk_session_id,
                    "model": "demo-agent",
                    "tools": spec.tools,
                    "permission_mode": "default",
                    "api_key_source": "demo",
                    "cwd": str(spec.cwd),
                },
            )
            if (fail := scenario.get("fail")) and (scenario.get("fail_role") in (None, spec.role)):
                await ctx.pause()
                return await ctx.failure(sdk_session_id, str(fail))

            resuming = spec.resume_sdk_session_id is not None
            continuing = spec.prompt.startswith(
                ("Your previous session was interrupted", "The previous attempt")
            )
            if spec.output_schema is not None:
                result, structured = await self._coordinate(ctx, spec)
            elif scenario.get("aggregate"):
                result, structured = await self._aggregate(ctx, spec), None
            elif resuming and not continuing:
                result, structured = await self._follow_up(ctx, spec, spec.prompt), None
            else:
                result, structured = await self._work(ctx, spec), None
            await ctx.turn_completed(result)

            while not io.inbox.empty() and not io.stop_event.is_set():
                text = io.inbox.get_nowait()
                await ctx.emit("user.message", {"text": text, "delivered": True})
                result = await self._follow_up(ctx, spec, text)
                await ctx.turn_completed(result)
        except _Stopped:
            return SessionOutcome(
                status=SessionStatus.INTERRUPTED,
                sdk_session_id=sdk_session_id,
                result="Stopped on request.",
                num_turns=ctx.tool_calls,
                model="demo-agent",
                api_key_source="demo",
                turns=ctx.turns,
            )
        return SessionOutcome(
            status=SessionStatus.COMPLETED,
            sdk_session_id=sdk_session_id,
            result=result,
            structured_output=structured,
            usage={"input_tokens": 1200 * ctx.tool_calls, "output_tokens": 180 * ctx.tool_calls},
            num_turns=ctx.tool_calls + 1,
            duration_ms=int(ctx.tool_calls * self.step_delay * 1000),
            model="demo-agent",
            api_key_source="demo",
            turns=ctx.turns,
        )

    # -- behaviours ----------------------------------------------------------
    async def _coordinate(self, ctx: _Ctx, spec: SessionSpec) -> tuple[str, dict[str, Any]]:
        await ctx.say("Reading the task and surveying the repository before planning.")
        await ctx.tool("Glob", {"pattern": "**/*"}, "README.md\nsrc/\ntests/")
        await ctx.tool("Grep", {"pattern": "TODO", "path": "."}, "3 matches")
        roles = spec.scenario.get("roles") or ["researcher", "implementer", "tester", "reviewer"]
        single = spec.scenario.get("strategy") == "single" or len(roles) == 1
        if single:
            role = "implementer" if "implementer" in roles else roles[0]
            plan = {
                "summary": "A focused change that one agent can complete end to end.",
                "strategy": "single",
                "rationale": "The task touches a small area; extra agents would add overhead.",
                "subtasks": [
                    {
                        "key": "s1",
                        "title": "Implement and verify the change",
                        "role": role,
                        "instructions": "Make the change, add or update tests, and verify.",
                        "depends_on": [],
                        "modifies_code": True,
                    }
                ],
            }
        else:
            subtasks = []
            if "researcher" in roles:
                subtasks.append(
                    {
                        "key": "s1",
                        "title": "Map the affected code",
                        "role": "researcher",
                        "instructions": "Find where the behaviour lives, list relevant files and risks.",
                        "depends_on": [],
                        "modifies_code": False,
                    }
                )
            impl_deps = ["s1"] if subtasks else []
            subtasks.append(
                {
                    "key": "s2",
                    "title": "Implement the change",
                    "role": "implementer" if "implementer" in roles else roles[0],
                    "instructions": "Implement the change described in the task using the research notes.",
                    "depends_on": impl_deps,
                    "modifies_code": True,
                }
            )
            if "tester" in roles:
                subtasks.append(
                    {
                        "key": "s3",
                        "title": "Add regression tests",
                        "role": "tester",
                        "instructions": "Write tests covering the new behaviour and run the suite.",
                        "depends_on": ["s2"],
                        "modifies_code": True,
                    }
                )
            if "reviewer" in roles:
                subtasks.append(
                    {
                        "key": "s4",
                        "title": "Review the implementation",
                        "role": "reviewer",
                        "instructions": "Review correctness, maintainability and risk of the change.",
                        "depends_on": ["s2"],
                        "modifies_code": False,
                    }
                )
            plan = {
                "summary": "Investigate, implement, then test and review in parallel.",
                "strategy": "team",
                "rationale": "Research de-risks the change; testing and review are independent once it lands.",
                "subtasks": subtasks,
            }
        await ctx.say(
            f"Plan ready: {len(plan['subtasks'])} subtask(s), strategy `{plan['strategy']}`."
        )
        return "Plan produced.", plan

    async def _aggregate(self, ctx: _Ctx, spec: SessionSpec) -> str:
        await ctx.say("Consolidating the team's reports.")
        return (
            "## Outcome\nThe change was implemented, tested and reviewed by the demo team.\n\n"
            "## What changed\n- Demo notes file updated in the worktree\n- Regression test added\n\n"
            "## Verification\n- Demo test command approved and executed (simulated)\n\n"
            "## Risks and follow-ups\n- This is demo output; run with Claude for real changes."
        )

    async def _work(self, ctx: _Ctx, spec: SessionSpec) -> str:
        role = spec.role
        slug = _slug(spec.scenario.get("task_title") or spec.task_id)
        if role in ("researcher", "reviewer"):
            await ctx.say("Exploring the codebase to understand the current behaviour.")
            await ctx.tool(
                "Glob", {"pattern": "**/*.py"}, "src/app.py\nsrc/util.py\ntests/test_app.py"
            )
            await ctx.tool(
                "Grep", {"pattern": "def ", "path": "src"}, "src/app.py:3: def handler()"
            )
            await ctx.tool("Read", {"file_path": str(spec.cwd / "README.md")}, "# Demo project …")
            verdict = (
                "Looks correct; consider an extra edge-case test."
                if role == "reviewer"
                else ("The behaviour lives in `src/app.py`; change is low risk.")
            )
            await ctx.say(verdict)
            return (
                f"## Summary\n{verdict}\n\n## Changes\nnone\n\n## Verification\nRead the relevant files.\n\n"
                "## Open questions / risks\nNone (demo)."
            )

        notes = Path("bosun-demo") / f"{slug}.md"
        if role == "tester":
            notes = Path("bosun-demo") / f"test_{slug.replace('-', '_')}.md"
        await ctx.say("Reading the relevant code before editing.")
        await ctx.tool("Read", {"file_path": str(spec.cwd / "README.md")}, "# Demo project …")
        await ctx.write(
            notes,
            f"# {role.title()} notes for {spec.task_id}\n\nDemo change made by the {role} agent.\n",
        )
        await ctx.tool(
            "Bash", {"command": "git status --short", "description": "Show changes"}, f"?? {notes}"
        )
        verification = "Checked `git status`."
        if spec.scenario.get("needs_approval") and role == "implementer":
            approved = await ctx.bash(
                "./scripts/migrate.sh --dry-run",
                "Preview the schema migration",
                "migration plan: 2 statements",
            )
            verification += " Migration dry-run " + (
                "approved and executed." if approved else "was denied."
            )
        if role in ("tester", "implementer"):
            approved = await ctx.bash("pytest -q", "Run the test suite", "12 passed in 0.41s")
            verification += (
                " Ran `pytest -q`: 12 passed."
                if approved
                else " Test run was denied by the operator."
            )
        await ctx.say("Done. Summarising.")
        return (
            f"## Summary\nDemo {role} work completed.\n\n## Changes\n- `{notes.as_posix()}`\n\n"
            f"## Verification\n{verification}\n\n## Open questions / risks\nDemo output only."
        )

    async def _follow_up(self, ctx: _Ctx, spec: SessionSpec, text: str) -> str:
        await ctx.say(f"Following up on: {text[:120]}")
        await ctx.tool("Read", {"file_path": str(spec.cwd / "README.md")}, "# Demo project …")
        if spec.role not in ("researcher", "reviewer", "coordinator"):
            await ctx.write(
                Path("bosun-demo") / "follow-up.md",
                f"Follow-up instruction handled:\n\n> {text}\n",
            )
        return f"## Summary\nHandled the follow-up instruction: {text[:200]}"


class _Ctx:
    def __init__(self, adapter: FakeAgentAdapter, spec: SessionSpec, io: SessionIO) -> None:
        self.adapter, self.spec, self.io = adapter, spec, io
        self.tool_calls = 0
        self.turns = 1
        self._n = 0

    async def pause(self) -> None:
        if self.io.stop_event.is_set():
            raise _Stopped
        if self.adapter.step_delay:
            try:
                await asyncio.wait_for(self.io.stop_event.wait(), self.adapter.step_delay)
            except TimeoutError:
                return
            raise _Stopped

    async def emit(self, type_: str, data: dict[str, Any], status: str | None = None) -> None:
        await self.io.emit(AgentEvent(type_, data, status=status))

    async def say(self, text: str) -> None:
        await self.pause()
        await self.emit("assistant.text", {"text": text, "parent_tool_use_id": None})

    def _tool_id(self) -> str:
        self._n += 1
        return f"toolu_demo_{self.spec.session_id[-6:]}_{self._n:03d}"

    async def tool(
        self, name: str, tool_input: dict[str, Any], output: str, *, is_error: bool = False
    ) -> str:
        await self.pause()
        tool_id = self._tool_id()
        self.tool_calls += 1
        await self.emit(
            "tool.call",
            {"tool_use_id": tool_id, "name": name, "input": tool_input, "parent_tool_use_id": None},
            status="running",
        )
        violation = boundary_violation(
            name, tool_input, self.spec.cwd, self.spec.allowed_roots, self.spec.protected_roots
        )
        if violation:
            await self.emit(
                "tool.denied", {"tool_use_id": tool_id, "tool": name, "reason": violation}
            )
            output, is_error = violation, True
        await self.pause()
        await self.emit(
            "tool.result",
            {
                "tool_use_id": tool_id,
                "name": name,
                "is_error": is_error,
                "output": output,
                "duration_ms": int(self.adapter.step_delay * 600),
                "parent_tool_use_id": None,
            },
            status="failed" if is_error else "completed",
        )
        return tool_id

    async def write(self, rel: Path, content: str) -> None:
        target = self.spec.cwd / rel
        await self.tool(
            "Write",
            {"file_path": str(target), "content": content},
            f"File created successfully at: {target}",
        )
        target.parent.mkdir(parents=True, exist_ok=True)
        existing = target.read_text() if target.exists() else ""
        target.write_text(existing + content)

    def _auto_allowed(self, command: str) -> bool:
        """Mirror Claude Code's `allowed_tools` matching for Bash rules."""
        for rule in self.spec.auto_allow:
            if rule == "Bash":
                return True
            if rule.startswith("Bash(") and rule.endswith(")"):
                spec = rule[5:-1]
                if spec.endswith(":*") and command.startswith(spec[:-2]):
                    return True
                if spec == command:
                    return True
        return False

    async def bash(self, command: str, description: str, output: str) -> bool:
        tool_input = {"command": command, "description": description}
        if self._auto_allowed(command):
            await self.tool("Bash", tool_input, output)
            return True
        return await self.permission("Bash", tool_input, output)

    async def permission(self, name: str, tool_input: dict[str, Any], output: str) -> bool:
        await self.pause()
        tool_id = self._tool_id()
        self.tool_calls += 1
        await self.emit(
            "tool.call",
            {"tool_use_id": tool_id, "name": name, "input": tool_input, "parent_tool_use_id": None},
            status="running",
        )
        decision = await self.io.request_permission(
            PermissionRequest(
                tool_name=name,
                tool_input=tool_input,
                tool_use_id=tool_id,
                title=f"Demo agent wants to run `{tool_input.get('command')}`",
            )
        )
        if self.io.stop_event.is_set():
            raise _Stopped
        await self.pause()
        await self.emit(
            "tool.result",
            {
                "tool_use_id": tool_id,
                "name": name,
                "is_error": not decision.allow,
                "output": output
                if decision.allow
                else f"Permission denied: {decision.message or 'denied'}",
                "duration_ms": 900,
                "parent_tool_use_id": None,
            },
            status="completed" if decision.allow else "failed",
        )
        return decision.allow

    async def turn_completed(self, result: str) -> None:
        await self.emit(
            "session.turn_completed",
            {
                "subtype": "success",
                "is_error": False,
                "num_turns": self.tool_calls,
                "result": result,
                "providers": ["demo"],
            },
            status="completed",
        )

    async def failure(self, sdk_session_id: str, kind: str) -> SessionOutcome:
        messages = {
            ErrorKind.AUTH_REQUIRED: "Not signed in to Claude (simulated). Run `claude auth login`.",
            ErrorKind.USAGE_LIMIT: "Claude usage limit reached (simulated).",
            ErrorKind.PROCESS_CRASH: "The agent process exited unexpectedly (simulated).",
        }
        error = messages.get(kind, f"Simulated failure: {kind}")
        await self.emit(
            "session.turn_completed",
            {"subtype": "error", "is_error": True, "result": error},
            status="failed",
        )
        return SessionOutcome(
            status=SessionStatus.FAILED,
            sdk_session_id=sdk_session_id,
            error=error,
            error_kind=kind
            if kind in ErrorKind.NON_RETRYABLE or kind == ErrorKind.PROCESS_CRASH
            else ErrorKind.UNKNOWN,
            model="demo-agent",
            api_key_source="demo",
        )
