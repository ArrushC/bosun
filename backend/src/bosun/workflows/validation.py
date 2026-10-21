"""Checks for an authored workflow, shared by the editor and run start.

Problems are reported, never fixed: an unknown agent or a code-changing step on
a read-only agent stops the run instead of being mapped to something else.
Errors block a run; warnings do not.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, Field

from bosun.domain.enums import PermissionProfile
from bosun.workflows.spec import MAX_STEPS, WorkflowSpec, WorkflowStep


@dataclass(frozen=True)
class AgentRef:
    name: str
    permission_profile: str
    enabled: bool
    is_coordinator: bool


class Issue(BaseModel):
    level: Literal["error", "warning"]
    code: str
    message: str
    # The steps the issue is about (keys); empty for the workflow as a whole.
    steps: list[str] = Field(default_factory=list)
    field: str | None = None


def errors(issues: list[Issue]) -> list[Issue]:
    return [i for i in issues if i.level == "error"]


def validate_workflow(spec: WorkflowSpec, agents: Mapping[str, AgentRef]) -> list[Issue]:
    issues: list[Issue] = []
    steps = spec.steps
    if not steps:
        return [Issue(level="error", code="no_steps", message="Add a step to run this workflow.")]
    if len(steps) > MAX_STEPS:
        issues.append(
            Issue(
                level="error",
                code="too_many_steps",
                message=f"A workflow can have up to {MAX_STEPS} steps; this one has {len(steps)}.",
            )
        )
    title = {s.key: s.title.strip() or "Untitled step" for s in steps}
    seen: set[str] = set()
    for s in steps:
        if s.key in seen:
            issues.append(
                Issue(
                    level="error",
                    code="duplicate_step",
                    steps=[s.key],
                    message="Two steps have the same ID.",
                )
            )
        seen.add(s.key)

    for s in steps:
        if not s.title.strip():
            issues.append(
                Issue(
                    level="error",
                    code="missing_title",
                    steps=[s.key],
                    field="title",
                    message="Give this step a name.",
                )
            )
        issues.extend(_agent_issues(s.key, s.agent, s.changes_code, agents))
        if not s.instructions.strip():
            issues.append(
                Issue(
                    level="warning",
                    code="missing_instructions",
                    steps=[s.key],
                    field="instructions",
                    message="Add instructions so the agent knows what this step is for.",
                )
            )
        for dep in s.after:
            if dep == s.key:
                issues.append(
                    Issue(
                        level="error",
                        code="self_dependency",
                        steps=[s.key],
                        field="after",
                        message="A step can't run after itself.",
                    )
                )
            elif dep not in title:
                issues.append(
                    Issue(
                        level="error",
                        code="unknown_dependency",
                        steps=[s.key],
                        field="after",
                        message=f"Runs after a step that no longer exists ({dep}).",
                    )
                )

    graph = {s.key: [d for d in dict.fromkeys(s.after) if d in title and d != s.key] for s in steps}
    cycle = _find_cycle(graph)
    if cycle:
        names = " → ".join(title[k] for k in [*cycle, cycle[0]])
        issues.append(
            Issue(
                level="error",
                code="cycle",
                steps=cycle,
                field="after",
                message=f"These steps wait for each other in a loop: {names}.",
            )
        )
    else:
        issues.extend(_parallel_code_changes(steps, graph, title))
    return issues


def _agent_issues(
    key: str, agent: str | None, changes_code: bool, agents: Mapping[str, AgentRef]
) -> list[Issue]:
    def issue(code: str, message: str) -> Issue:
        return Issue(level="error", code=code, steps=[key], field="agent", message=message)

    if not agent:
        return [issue("missing_agent", "Choose an agent for this step.")]
    ref = agents.get(agent)
    if ref is None:
        return [
            issue("unknown_agent", f"There is no agent called '{agent}'. Choose another agent.")
        ]
    if ref.is_coordinator:
        return [
            issue(
                "coordinator_agent",
                f"The {ref.name} plans agent-team runs and can't be a workflow step. Choose another agent.",
            )
        ]
    if not ref.enabled:
        return [
            issue(
                "disabled_agent",
                f"The {ref.name} agent is turned off. Turn it on in Settings, or choose another agent.",
            )
        ]
    if changes_code and ref.permission_profile == PermissionProfile.READ_ONLY:
        return [
            Issue(
                level="error",
                code="read_only_changes_code",
                steps=[key],
                field="changes_code",
                message=(
                    f"This step changes code, but the {ref.name} agent can only read files. "
                    "Choose an agent that can edit files, or turn off Changes code."
                ),
            )
        ]
    return []


def _find_cycle(graph: Mapping[str, list[str]]) -> list[str]:
    """Return the steps of one dependency loop, in order, or [] if there is none."""
    state: dict[str, int] = {}  # 1 = on the current path, 2 = done
    path: list[str] = []

    def visit(node: str) -> list[str]:
        state[node] = 1
        path.append(node)
        for dep in graph.get(node, []):
            if state.get(dep) == 1:
                return path[path.index(dep) :]
            if dep not in state and (found := visit(dep)):
                return found
        path.pop()
        state[node] = 2
        return []

    for node in graph:
        if node not in state and (found := visit(node)):
            return list(reversed(found))
    return []


def _parallel_code_changes(
    steps: list[WorkflowStep], graph: Mapping[str, list[str]], title: Mapping[str, str]
) -> list[Issue]:
    ancestors: dict[str, set[str]] = {}

    def upstream(key: str) -> set[str]:
        if key not in ancestors:
            ancestors[key] = set()
            for dep in graph.get(key, []):
                ancestors[key] |= {dep, *upstream(dep)}
        return ancestors[key]

    writers = [s.key for s in steps if s.changes_code]
    issues = []
    for i, a in enumerate(writers):
        for b in writers[i + 1 :]:
            if a in upstream(b) or b in upstream(a):
                continue
            issues.append(
                Issue(
                    level="warning",
                    code="parallel_code_changes",
                    steps=[a, b],
                    message=(
                        f"{title[a]} and {title[b]} both change code and can run at the same time; "
                        "their changes may conflict when merged."
                    ),
                )
            )
    return issues
