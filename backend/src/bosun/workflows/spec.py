"""Authored workflows: a graph of agent steps that runs on a board task.

A step runs one agent with its own instructions. `after` lists the steps it
waits for: steps with nothing between them run at the same time, and a step
with several entries waits for all of them. The run starts with the steps that
wait for nothing and ends when no step can run any more.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

STEP_KEY_PATTERN = r"^[a-z0-9][a-z0-9_-]{0,39}$"
MAX_STEPS = 20


class StepPosition(BaseModel):
    x: float = 0
    y: float = 0


class WorkflowStep(BaseModel):
    key: str = Field(pattern=STEP_KEY_PATTERN)
    title: str = Field(default="", max_length=120)
    agent: str | None = None
    instructions: str = Field(default="", max_length=8000)
    changes_code: bool = False
    after: list[str] = Field(default_factory=list, max_length=50)
    position: StepPosition = Field(default_factory=StepPosition)
    # Optional overrides of the agent's defaults.
    model: str | None = Field(default=None, max_length=100)
    max_turns: int | None = Field(default=None, ge=1, le=500)
    timeout_minutes: int | None = Field(default=None, ge=1, le=600)
    retries: int | None = Field(default=None, ge=0, le=4)


class WorkflowSpec(BaseModel):
    # Drafts may hold more than MAX_STEPS while being edited; validation reports it.
    steps: list[WorkflowStep] = Field(default_factory=list, max_length=50)


class WorkflowSnapshot(BaseModel):
    """The draft a run uses, frozen when the run starts."""

    id: str
    name: str
    revision: int
    steps: list[WorkflowStep]

    def step(self, key: str) -> WorkflowStep | None:
        return next((s for s in self.steps if s.key == key), None)
