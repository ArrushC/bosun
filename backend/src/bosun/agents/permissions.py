"""Permission profiles and workspace-boundary enforcement.

Profiles map to three Agent SDK controls:

* `tools` — the built-in tools that exist in the session at all,
* `allowed_tools` — rules that run without asking (everything else that
  would prompt is routed to Bosun's approval queue via `can_use_tool`),
* `disallowed_tools` — tools removed outright.

Independently, a `PreToolUse` hook denies any file-tool call whose path leaves
the session's allowed roots (its worktree), regardless of profile.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from bosun.domain.enums import PermissionProfile
from bosun.settings_store import ExecutionSettings

READ_TOOLS = ["Read", "Glob", "Grep"]
EDIT_TOOLS = ["Edit", "Write", "NotebookEdit"]
# Tools that need a human-facing interaction model Bosun does not provide.
ALWAYS_DISALLOWED = [
    "AskUserQuestion",
    "EnterPlanMode",
    "ExitPlanMode",
    "EnterWorktree",
    "ExitWorktree",
]
WEB_TOOLS = ["WebFetch", "WebSearch"]
SUBAGENT_TOOLS = ["Agent", "Task"]

PATH_KEYS = ("file_path", "path", "notebook_path")


@dataclass
class ToolPolicy:
    tools: list[str]
    auto_allow: list[str]
    disallowed: list[str]
    description: str


def tool_policy(
    profile: str,
    execution: ExecutionSettings,
    *,
    extra_allowed: list[str] | None = None,
    allow_subagents: bool = False,
) -> ToolPolicy:
    profile = PermissionProfile(profile)
    tools = list(READ_TOOLS) + ["TodoWrite"]
    auto_allow = list(READ_TOOLS) + ["TodoWrite"]
    disallowed = list(ALWAYS_DISALLOWED)
    description = "Read-only: can read and search files in its worktree."

    if profile in (
        PermissionProfile.EDIT,
        PermissionProfile.EDIT_AND_TEST,
        PermissionProfile.TRUSTED_SHELL,
    ):
        tools += EDIT_TOOLS + ["Bash"]
        auto_allow += EDIT_TOOLS + list(execution.auto_allow_bash)
        description = "Can edit files in its worktree. Shell commands outside the safe list need your approval."
    if profile == PermissionProfile.EDIT_AND_TEST:
        auto_allow += list(execution.test_commands)
        description = (
            "Can edit files and run configured test commands. Other shell commands need approval."
        )
    if profile == PermissionProfile.TRUSTED_SHELL:
        auto_allow += ["Bash"]
        description = "Can edit files and run any shell command in its worktree without asking."

    if execution.allow_web_tools:
        tools += WEB_TOOLS
    else:
        disallowed += WEB_TOOLS

    if allow_subagents:
        tools += ["Agent"]
        auto_allow += ["Agent"]
    else:
        disallowed += SUBAGENT_TOOLS

    if extra_allowed:
        auto_allow += [t for t in extra_allowed if t not in auto_allow]
        # A role-specific shell rule (e.g. read-only git commands for a reviewer)
        # makes the shell available; commands outside the rules still need approval.
        if any(t == "Bash" or t.startswith("Bash(") for t in extra_allowed) and "Bash" not in tools:
            tools.append("Bash")
    return ToolPolicy(
        tools=list(dict.fromkeys(tools)),
        auto_allow=list(dict.fromkeys(auto_allow)),
        disallowed=list(dict.fromkeys(disallowed)),
        description=description,
    )


_GLOB_CHARS = re.compile(r"[*?\[{]")


def tool_paths(tool_name: str, tool_input: dict[str, Any]) -> list[str]:
    paths = []
    for key in PATH_KEYS:
        value = tool_input.get(key)
        if isinstance(value, str) and value:
            paths.append(value)
    pattern = tool_input.get("pattern")
    if tool_name == "Glob" and isinstance(pattern, str) and pattern:
        # The literal prefix of a glob pattern is a directory it will read.
        prefix = _GLOB_CHARS.split(pattern, maxsplit=1)[0]
        if prefix.startswith(("/", "~")) or ".." in Path(prefix).parts:
            paths.append(prefix.rsplit("/", 1)[0] if "/" in prefix[1:] else prefix)
    return paths


def _inside(resolved: Path, roots: list[Path]) -> bool:
    return any(resolved == root or resolved.is_relative_to(root) for root in roots)


def outside_roots(path: str, cwd: Path, roots: list[Path]) -> bool:
    candidate = Path(path).expanduser()
    if not candidate.is_absolute():
        candidate = cwd / candidate
    try:
        resolved = candidate.resolve(strict=False)
    except (OSError, RuntimeError):
        return True
    return not _inside(resolved, roots)


# Path-like words in a shell command: ~/…, $HOME/…, $PWD/…, absolute paths, and
# any relative path with a `..` segment (`../x`, `./../x`, `src/../../x`).
_SHELL_PATH = re.compile(
    r"""(?:^|(?<=[\s=:'"(<>]))(
        (?:~|\$HOME|\$\{HOME\}|\$PWD|\$\{PWD\}|\$\(pwd\)|`pwd`)(?:/[^\s'";|&<>)]*)?
      | /[^\s'";|&<>)]+
      | [^\s'";|&<>()$`]*(?:^|/|(?<=[\s=:'"(<>]))\.\.(?:/[^\s'";|&<>)]*)?(?=$|[\s'";|&<>)])
    )""",
    re.VERBOSE,
)
# `cd` with no argument, `cd ~`, `cd -`, `pushd`/`popd` all leave the working directory.
_CD_AWAY = re.compile(
    r"(?:^|[;&|(]\s*|\bthen\s+|\bdo\s+)(?:cd|pushd|popd)(?:\s+(?:-|~)?\s*)?(?=$|[;&|)])"
)


def shell_paths_outside(
    command: str, cwd: Path, roots: list[Path], protected: list[Path] | None = None
) -> list[str]:
    """Paths a shell command references outside the allowed roots that it must not touch.

    A heuristic, defence-in-depth check, not a sandbox: Claude Code
    auto-approves read-only commands such as `cat` or `find`, so file reads
    through the shell would otherwise bypass the file-tool boundary. Blocked:
    the user's home directory (credentials, other projects), the protected
    roots (the user's own checkout and Bosun's data), and `..`
    escapes. System paths such as `/usr`, `/tmp` and `/dev` are not blocked.
    Variables, `eval` and scripts can still reach other paths; enable the
    Claude Code sandbox for an OS-level boundary.
    """
    home = Path.home().resolve()
    protected = protected or []
    offending = []
    if _CD_AWAY.search(command):
        offending.append("cd (leaves the working directory)")
    for match in _SHELL_PATH.finditer(command):
        raw = match.group(1)
        expanded = (
            raw.replace("${HOME}", str(home))
            .replace("$HOME", str(home))
            .replace("${PWD}", str(cwd))
            .replace("$PWD", str(cwd))
            .replace("$(pwd)", str(cwd))
            .replace("`pwd`", str(cwd))
        )
        candidate = Path(expanded).expanduser()
        if not candidate.is_absolute():
            candidate = cwd / candidate
        try:
            resolved = candidate.resolve(strict=False)
        except (OSError, RuntimeError):
            continue
        if _inside(resolved, roots):
            continue
        if (
            resolved == home
            or resolved.is_relative_to(home)
            or ".." in Path(expanded).parts
            or _inside(resolved, protected)
        ):
            offending.append(raw)
    return offending


def boundary_violation(
    tool_name: str,
    tool_input: dict[str, Any],
    cwd: Path,
    roots: list[Path],
    protected: list[Path] | None = None,
) -> str | None:
    """Return a denial reason if a tool call targets a path outside the allowed roots."""
    resolved_roots = [r.resolve() for r in roots]
    resolved_protected = [p.resolve() for p in protected or []]
    for path in tool_paths(tool_name, tool_input):
        if outside_roots(path, cwd, resolved_roots):
            return (
                f"{tool_name} blocked by Bosun: '{path}' is outside this agent's "
                f"workspace ({cwd}). Work only inside the current working directory."
            )
    command = tool_input.get("command")
    if tool_name == "Bash" and isinstance(command, str):
        offending = shell_paths_outside(command, cwd, resolved_roots, resolved_protected)
        if offending:
            return (
                f"Bash blocked by Bosun: the command references {', '.join(offending[:3])}, "
                f"outside this agent's workspace ({cwd}). Use paths inside the working directory."
            )
    return None
