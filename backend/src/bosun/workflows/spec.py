"""Authored workflows: a graph of steps that runs on a board task.

An agent step runs one agent with its own instructions. An approval step runs
no agent: it waits for a person to approve or reject, with its instructions
telling them what to check. `after` lists the steps a step waits for: steps
with nothing between them run at the same time, and a step with several
entries waits for all of them. The run starts with the steps that wait for
nothing and ends when no step can run any more.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, model_validator

STEP_KEY_PATTERN = r"^[a-z0-9][a-z0-9_-]{0,39}$"
MAX_STEPS = 20


class StepPosition(BaseModel):
    x: float = 0
    y: float = 0


StepKind = Literal["agent", "approval"]


class WorkflowStep(BaseModel):
    key: str = Field(pattern=STEP_KEY_PATTERN)
    kind: StepKind = "agent"
    title: str = Field(default="", max_length=120)
    agent: str | None = None
    instructions: str = Field(default="", max_length=8000)
    changes_code: bool = False
    after: list[str] = Field(default_factory=list, max_length=50)
    position: StepPosition = Field(default_factory=StepPosition)
    # Optional overrides of the agent's defaults (agent steps only).
    model: str | None = Field(default=None, max_length=100)
    max_turns: int | None = Field(default=None, ge=1, le=500)
    timeout_minutes: int | None = Field(default=None, ge=1, le=600)
    retries: int | None = Field(default=None, ge=0, le=4)

    @model_validator(mode="after")
    def _approval_has_no_agent(self) -> WorkflowStep:
        # An approval step is a person's decision: agent settings would never apply.
        if self.kind == "approval":
            self.agent = None
            self.changes_code = False
            self.model = self.max_turns = self.timeout_minutes = self.retries = None
        return self

    @property
    def is_approval(self) -> bool:
        return self.kind == "approval"


class WorkflowSpec(BaseModel):
    # Drafts may hold more than MAX_STEPS while being edited; validation reports it.
    steps: list[WorkflowStep] = Field(default_factory=list, max_length=50)


class WorkflowSnapshot(BaseModel):
    """The workflow a run uses, frozen when the run starts.

    `version` is the published version it ran, or None for the draft (every run
    before versions existed ran the draft). `revision` is the draft revision the
    steps came from.
    """

    id: str
    name: str
    revision: int
    version: int | None = None
    steps: list[WorkflowStep]

    def step(self, key: str) -> WorkflowStep | None:
        return next((s for s in self.steps if s.key == key), None)
