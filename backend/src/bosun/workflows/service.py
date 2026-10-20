"""Loading, checking and describing stored workflows."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bosun.db.models import AgentRole, Workflow
from bosun.workflows.spec import WorkflowSnapshot, WorkflowSpec
from bosun.workflows.validation import AgentRef, Issue, errors, validate_workflow


class WorkflowInvalid(ValueError):
    """A workflow with errors was asked to run."""

    def __init__(self, issues: list[Issue], spec: WorkflowSpec) -> None:
        self.issues = issues
        super().__init__(describe_errors(issues, spec))


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


def spec_of(workflow: Workflow) -> WorkflowSpec:
    return WorkflowSpec.model_validate(workflow.spec or {})


async def check(session: AsyncSession, workflow: Workflow) -> list[Issue]:
    return validate_workflow(spec_of(workflow), await agent_refs(session))


async def snapshot_for_run(session: AsyncSession, workflow: Workflow) -> WorkflowSnapshot:
    """The frozen copy a run executes; raises WorkflowInvalid if it has errors."""
    spec = spec_of(workflow)
    issues = validate_workflow(spec, await agent_refs(session))
    if errors(issues):
        raise WorkflowInvalid(issues, spec)
    return WorkflowSnapshot(
        id=workflow.id, name=workflow.name, revision=workflow.revision, steps=spec.steps
    )


def describe_errors(issues: list[Issue], spec: WorkflowSpec) -> str:
    found = errors(issues)
    if not found:
        return "The workflow has no problems."
    titles = {s.key: s.title.strip() or s.key for s in spec.steps}
    first = found[0]
    where = ", ".join(titles.get(k, k) for k in first.steps)
    text = f"{where}: {first.message}" if where else first.message
    more = len(found) - 1
    suffix = f" ({more} more problem{'s' if more > 1 else ''} to fix.)" if more else ""
    return f"Fix this workflow before running it. {text}{suffix}"
