"""Loading, checking, publishing and describing stored workflows.

A workflow has one editable draft (the `workflows` row) and any number of
published versions. A version is an immutable copy of the draft with the
definitions of the agents its steps use frozen in, so running version 3 next
month does what it did the day it was published. Runs freeze whichever they
execute (a version or the draft) into their own configuration.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from bosun.db.models import AgentRole, Workflow, WorkflowVersion
from bosun.orchestration.config import RoleConfig
from bosun.workflows.spec import WorkflowSnapshot, WorkflowSpec
from bosun.workflows.validation import AgentRef, Issue, errors, validate_workflow


class WorkflowInvalid(ValueError):
    """A workflow with errors was asked to run or be published."""

    def __init__(self, issues: list[Issue], spec: WorkflowSpec, action: str = "running") -> None:
        self.issues = issues
        super().__init__(describe_errors(issues, spec, action))


class NothingToPublish(ValueError):
    pass


async def agent_refs(session: AsyncSession) -> dict[str, AgentRef]:
    """Every agent, enabled or not, so validation can say which one is off."""
    roles = (await session.scalars(select(AgentRole))).all()
    return {
        r.key: AgentRef(
            name=r.name,
            permission_profile=r.permission_profile,
            enabled=r.enabled,
            is_coordinator=r.is_coordinator,
        )
        for r in roles
    }


def role_config(r: AgentRole) -> RoleConfig:
    return RoleConfig(
        key=r.key,
        name=r.name,
        description=r.description,
        system_prompt=r.system_prompt,
        permission_profile=r.permission_profile,
        model=r.model,
        effort=r.effort,
        max_turns=r.max_turns,
        extra_allowed_tools=list(r.extra_allowed_tools),
        is_coordinator=r.is_coordinator,
        color=r.color,
    )


def spec_of(row: Workflow | WorkflowVersion) -> WorkflowSpec:
    return WorkflowSpec.model_validate(row.spec or {})


async def check(session: AsyncSession, workflow: Workflow) -> list[Issue]:
    return validate_workflow(spec_of(workflow), await agent_refs(session))


# -- versions ---------------------------------------------------------------------------------
async def latest_version(session: AsyncSession, workflow_id: str) -> WorkflowVersion | None:
    return await session.scalar(
        select(WorkflowVersion)
        .where(WorkflowVersion.workflow_id == workflow_id)
        .order_by(WorkflowVersion.number.desc())
    )


async def get_version(
    session: AsyncSession, workflow_id: str, number: int
) -> WorkflowVersion | None:
    return await session.scalar(
        select(WorkflowVersion).where(
            WorkflowVersion.workflow_id == workflow_id, WorkflowVersion.number == number
        )
    )


def _content(name: str, description: str, spec: WorkflowSpec) -> dict[str, Any]:
    return {"name": name, "description": description, "spec": spec.model_dump(mode="json")}


def matches(workflow: Workflow, version: WorkflowVersion | None) -> bool:
    """True when the draft's name, description and steps are exactly those of `version`."""
    return version is not None and _content(
        workflow.name, workflow.description, spec_of(workflow)
    ) == _content(version.name, version.description, spec_of(version))


async def frozen_agents(session: AsyncSession, spec: WorkflowSpec) -> dict[str, dict[str, Any]]:
    """The current definitions of the agents `spec`'s steps use, keyed by agent."""
    keys = list(dict.fromkeys(s.agent for s in spec.steps if s.agent and not s.is_approval))
    rows = (await session.scalars(select(AgentRole).where(AgentRole.key.in_(keys)))).all()
    by_key = {r.key: r for r in rows}
    return {k: role_config(by_key[k]).model_dump(mode="json") for k in keys if k in by_key}


async def publish(session: AsyncSession, workflow: Workflow, note: str = "") -> WorkflowVersion:
    """Publish the draft as the next version. The caller commits; the number is unique per workflow."""
    spec = spec_of(workflow)
    issues = validate_workflow(spec, await agent_refs(session))
    if errors(issues):
        raise WorkflowInvalid(issues, spec, "publishing")
    agents = await frozen_agents(session, spec)
    latest = await latest_version(session, workflow.id)
    if latest is not None and matches(workflow, latest) and latest.agents == agents:
        raise NothingToPublish(
            f"Nothing has changed since version {latest.number}, and its agents are the same."
        )
    number = (
        await session.scalar(
            select(func.max(WorkflowVersion.number)).where(
                WorkflowVersion.workflow_id == workflow.id
            )
        )
        or 0
    ) + 1
    row = WorkflowVersion(
        workflow_id=workflow.id,
        number=number,
        name=workflow.name,
        description=workflow.description,
        spec=spec.model_dump(mode="json"),
        agents=agents,
        note=note.strip(),
        draft_revision=workflow.revision,
    )
    session.add(row)
    return row


class AgentChange(BaseModel):
    """How an agent a version uses differs from that agent today."""

    key: str
    name: str
    # changed: still available but defined differently; off and missing: the version can't run.
    state: str
    fields: list[str] = []


# What a person would call each part of an agent's definition.
_AGENT_FIELDS = {
    "name": "name",
    "system_prompt": "instructions",
    "permission_profile": "permissions",
    "extra_allowed_tools": "allowed tools",
    "model": "model",
    "effort": "effort",
    "max_turns": "turn limit",
}


async def agent_changes(session: AsyncSession, version: WorkflowVersion) -> list[AgentChange]:
    rows = (
        await session.scalars(select(AgentRole).where(AgentRole.key.in_(list(version.agents))))
    ).all()
    current = {r.key: r for r in rows}
    out: list[AgentChange] = []
    for key, data in version.agents.items():
        frozen = RoleConfig.model_validate(data)
        row = current.get(key)
        if row is None or row.is_coordinator:
            out.append(AgentChange(key=key, name=frozen.name, state="missing"))
            continue
        now = role_config(row).model_dump()
        then = frozen.model_dump()
        fields = [label for f, label in _AGENT_FIELDS.items() if now.get(f) != then.get(f)]
        if not row.enabled:
            out.append(AgentChange(key=key, name=row.name, state="off", fields=fields))
        elif fields:
            out.append(AgentChange(key=key, name=frozen.name, state="changed", fields=fields))
    return out


def version_issues(changes: list[AgentChange], spec: WorkflowSpec) -> list[Issue]:
    """Why a published version can't run now (its steps were checked when it was published)."""
    issues = []
    for c in changes:
        if c.state == "missing":
            message = f"The {c.name} agent no longer exists. Publish a new version that uses another agent."
        elif c.state == "off":
            message = f"The {c.name} agent is turned off. Turn it on in Settings, or publish a new version without it."
        else:
            continue
        steps = [s.key for s in spec.steps if s.agent == c.key]
        issues.append(
            Issue(
                level="error", code=f"agent_{c.state}", steps=steps, field="agent", message=message
            )
        )
    return issues


# -- running ----------------------------------------------------------------------------------
class RunSource(BaseModel):
    """What a run executes: the frozen workflow, and the agents to use for its steps."""

    snapshot: WorkflowSnapshot
    # The agents frozen into a published version; empty for a draft, which uses today's agents.
    agents: dict[str, RoleConfig] = {}


async def source_for_run(
    session: AsyncSession, workflow: Workflow, version: int | None = None
) -> RunSource:
    """The frozen copy a run executes (the draft, or a published version).

    Raises WorkflowInvalid when it can't run, and LookupError for a version that doesn't exist.
    """
    if version is None:
        spec = spec_of(workflow)
        issues = validate_workflow(spec, await agent_refs(session))
        if errors(issues):
            raise WorkflowInvalid(issues, spec)
        return RunSource(
            snapshot=WorkflowSnapshot(
                id=workflow.id, name=workflow.name, revision=workflow.revision, steps=spec.steps
            )
        )
    row = await get_version(session, workflow.id, version)
    if row is None:
        raise LookupError(f"This workflow has no version {version}")
    spec = spec_of(row)
    issues = version_issues(await agent_changes(session, row), spec)
    if errors(issues):
        raise WorkflowInvalid(issues, spec)
    return RunSource(
        snapshot=WorkflowSnapshot(
            id=workflow.id,
            name=row.name,
            revision=row.draft_revision,
            version=row.number,
            steps=spec.steps,
        ),
        agents={k: RoleConfig.model_validate(v) for k, v in row.agents.items()},
    )


def describe_errors(issues: list[Issue], spec: WorkflowSpec, action: str = "running") -> str:
    found = errors(issues)
    if not found:
        return "The workflow has no problems."
    titles = {s.key: s.title.strip() or "Untitled step" for s in spec.steps}
    first = found[0]
    where = ", ".join(titles.get(k, k) for k in first.steps)
    text = f"{where}: {first.message}" if where else first.message
    more = len(found) - 1
    suffix = f" ({more} more problem{'s' if more > 1 else ''} to fix.)" if more else ""
    return f"Fix this workflow before {action} it. {text}{suffix}"
