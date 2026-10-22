"""Built-in workflow templates.

Creating a workflow from a template copies the template's steps into a new,
independent draft: later changes to either never reach the other. Templates use
the built-in agents by key; `setup` reports what still needs configuring here
before a workflow made from one can run.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel

from bosun.domain.enums import PermissionProfile
from bosun.workflows.spec import StepPosition, WorkflowStep
from bosun.workflows.validation import AgentRef

ROW = 140
COLUMN = 280


def _step(
    key: str,
    title: str,
    agent: str,
    instructions: str,
    *after: str,
    changes_code: bool = False,
    x: float = 0,
    row: int = 0,
) -> WorkflowStep:
    return WorkflowStep(
        key=key,
        title=title,
        agent=agent,
        instructions=instructions,
        changes_code=changes_code,
        after=list(after),
        position=StepPosition(x=x, y=row * ROW),
    )


@dataclass(frozen=True)
class Template:
    key: str
    name: str
    description: str
    # The steps in order, as the template card shows them.
    outline: str
    steps: tuple[WorkflowStep, ...]


TEMPLATES: tuple[Template, ...] = (
    Template(
        key="fix-with-review",
        name="Fix with review",
        description="Find the cause, fix it, then test and review the change side by side before a final check.",
        outline="Research → Implement → Add tests and Review → Final check",
        steps=(
            _step(
                "research",
                "Research",
                "researcher",
                "Find where the problem described in the task comes from. Read the relevant code and "
                "the tests around it, explain the cause with file and line references, and suggest "
                "the smallest change that fixes it. Don't change any files.",
            ),
            _step(
                "implement",
                "Implement",
                "implementer",
                "Fix the problem, using the cause found in the research step. Keep the change as "
                "small as the fix allows, follow the code's existing style, and run the existing "
                "tests that cover it.",
                "research",
                changes_code=True,
                row=1,
            ),
            _step(
                "add-tests",
                "Add tests",
                "tester",
                "Add or update tests that fail without the fix and pass with it. Run the tests for "
                "the affected area and report the results.",
                "implement",
                changes_code=True,
                x=-COLUMN / 2,
                row=2,
            ),
            _step(
                "review",
                "Review",
                "reviewer",
                "Review the change for correctness, edge cases and unintended effects. List what "
                "must be fixed before merging separately from optional suggestions. Don't change "
                "any files.",
                "implement",
                x=COLUMN / 2,
                row=2,
            ),
            _step(
                "final-check",
                "Final check",
                "reviewer",
                "Compare the test results with the review. Say whether the change is ready to "
                "merge, and list anything left to do.",
                "add-tests",
                "review",
                row=3,
            ),
        ),
    ),
    Template(
        key="research-report",
        name="Research report",
        description="Research the code and the docs side by side, then combine what they found into one report. Changes no files.",
        outline="Read the code and the docs side by side → Write the report",
        steps=(
            _step(
                "read-code",
                "Read the code",
                "researcher",
                "Study the code that matters for the task's question. Explain how it works today, "
                "with file and line references, and note anything surprising or risky. Don't "
                "change any files.",
                x=-COLUMN / 2,
            ),
            _step(
                "read-docs",
                "Read the docs",
                "researcher",
                "Study the project's documentation, comments, changelog and tests for anything "
                "that matters for the task's question. Note where they may disagree with the "
                "code. Don't change any files.",
                x=COLUMN / 2,
            ),
            _step(
                "report",
                "Write the report",
                "researcher",
                "Combine the two earlier reports into one answer to the task's question: a short "
                "summary first, then the details with references, then open questions. Point out "
                "where the code and the documentation disagree.",
                "read-code",
                "read-docs",
                row=1,
            ),
        ),
    ),
    Template(
        key="implement-and-test",
        name="Implement and test",
        description="Make the change, then add tests for it and run them.",
        outline="Implement → Test",
        steps=(
            _step(
                "implement",
                "Implement",
                "implementer",
                "Make the change the task describes. Keep it focused, follow the code's existing "
                "style, and run the tests that cover the area you changed.",
                changes_code=True,
            ),
            _step(
                "test",
                "Test",
                "tester",
                "Add or update tests for the change, then run them with the rest of the affected "
                "test suite and report the results. Change only test code; report any problem "
                "you find in the change itself.",
                "implement",
                changes_code=True,
                row=1,
            ),
        ),
    ),
)


def template(key: str) -> Template | None:
    return next((t for t in TEMPLATES if t.key == key), None)


class SetupItem(BaseModel):
    """Something to configure before a workflow made from the template can run."""

    level: Literal["required", "recommended"]
    done: bool
    message: str
    # Where to fix it in the app.
    link: str | None = None


def setup(
    t: Template, agents: Mapping[str, AgentRef], *, workspaces: int, test_commands: int
) -> list[SetupItem]:
    items: list[SetupItem] = []
    for key in dict.fromkeys(s.agent for s in t.steps if s.agent):
        ref = agents.get(key)
        name = ref.name if ref else key.title()
        if ref is None or ref.is_coordinator:
            items.append(
                SetupItem(
                    level="required",
                    done=False,
                    message=f"There is no {name} agent. Create one in Settings, or choose another agent for its steps.",
                    link="/settings",
                )
            )
        else:
            items.append(
                SetupItem(
                    level="required",
                    done=ref.enabled,
                    message=f"The {name} agent is turned on"
                    if ref.enabled
                    else f"Turn on the {name} agent in Settings, or choose another agent for its steps.",
                    link=None if ref.enabled else "/settings",
                )
            )
    items.append(
        SetupItem(
            level="required",
            done=workspaces > 0,
            message="A workspace to run in"
            if workspaces
            else "Add a workspace in Integrations: every run works in a project folder.",
            link=None if workspaces else "/integrations",
        )
    )
    testers = [
        s
        for s in t.steps
        if s.changes_code
        and (ref := agents.get(s.agent or ""))
        and ref.permission_profile == PermissionProfile.EDIT_AND_TEST
    ]
    if testers:
        items.append(
            SetupItem(
                level="recommended",
                done=test_commands > 0,
                message="Test commands that agents may run without asking"
                if test_commands
                else "Add your test commands in Settings, so agents can run tests without asking you each time.",
                link=None if test_commands else "/settings",
            )
        )
    return items
