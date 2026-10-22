"""Explicit state machines for tasks, runs and subtasks.

The tables are the single source of truth for allowed transitions; services
call `ensure_*_transition` before persisting a change. See docs/ARCHITECTURE.md
("State machines") for the rendered diagrams.
"""

from __future__ import annotations

from bosun.domain.enums import RunStatus, SubtaskStatus, TaskStatus


class InvalidTransition(ValueError):
    def __init__(self, entity: str, current: str, target: str, reason: str = "") -> None:
        self.entity, self.current, self.target = entity, current, target
        detail = f"{entity} cannot move from '{current}' to '{target}'"
        super().__init__(f"{detail}: {reason}" if reason else detail)


# --- Tasks -------------------------------------------------------------------
# Humans may move cards freely between planning columns. Two rules protect the
# meaning of the board:
#   * DONE is a human decision: agents never move a task to DONE, and a task
#     with an active run cannot be marked DONE.
#   * Agent-driven transitions are limited to the `SYSTEM_TASK_TRANSITIONS` set.
T = TaskStatus
HUMAN_TASK_TRANSITIONS: dict[TaskStatus, frozenset[TaskStatus]] = {
    T.BACKLOG: frozenset({T.READY, T.IN_PROGRESS, T.BLOCKED, T.DONE}),
    T.READY: frozenset({T.BACKLOG, T.IN_PROGRESS, T.BLOCKED, T.DONE}),
    T.IN_PROGRESS: frozenset({T.BACKLOG, T.READY, T.REVIEW, T.BLOCKED, T.DONE}),
    T.REVIEW: frozenset({T.IN_PROGRESS, T.READY, T.BLOCKED, T.DONE, T.BACKLOG}),
    T.BLOCKED: frozenset({T.BACKLOG, T.READY, T.IN_PROGRESS, T.REVIEW}),
    T.DONE: frozenset({T.BACKLOG, T.READY, T.REVIEW}),
}

SYSTEM_TASK_TRANSITIONS: dict[TaskStatus, frozenset[TaskStatus]] = {
    # A run starts: work is now in progress.
    T.BACKLOG: frozenset({T.IN_PROGRESS}),
    T.READY: frozenset({T.IN_PROGRESS}),
    T.REVIEW: frozenset({T.IN_PROGRESS}),
    T.BLOCKED: frozenset({T.IN_PROGRESS}),
    # A run finishes: results need human review, the task is blocked on a failure,
    # or the run was cancelled and the task returns to Ready.
    T.IN_PROGRESS: frozenset({T.REVIEW, T.BLOCKED, T.READY}),
    T.DONE: frozenset(),
}


def ensure_task_transition(
    current: TaskStatus, target: TaskStatus, *, actor: str, has_active_run: bool = False
) -> None:
    if current == target:
        return
    table = HUMAN_TASK_TRANSITIONS if actor == "human" else SYSTEM_TASK_TRANSITIONS
    if target not in table[current]:
        raise InvalidTransition("Task", current, target, f"not allowed for {actor}")
    if target == T.DONE and has_active_run:
        raise InvalidTransition("Task", current, target, "an agent run is still active")


# --- Runs --------------------------------------------------------------------
R = RunStatus
RUN_TRANSITIONS: dict[RunStatus, frozenset[RunStatus]] = {
    R.QUEUED: frozenset({R.PREPARING, R.CANCELLED, R.FAILED, R.INTERRUPTED}),
    R.PREPARING: frozenset({R.PLANNING, R.RUNNING, R.FAILED, R.CANCELLED, R.INTERRUPTED}),
    R.PLANNING: frozenset(
        {R.WAITING_APPROVAL, R.RUNNING, R.FAILED, R.CANCELLED, R.INTERRUPTED, R.PAUSED}
    ),
    R.RUNNING: frozenset(
        {R.WAITING_APPROVAL, R.PAUSED, R.COMPLETED, R.FAILED, R.CANCELLED, R.INTERRUPTED}
    ),
    R.WAITING_APPROVAL: frozenset(
        {R.RUNNING, R.PLANNING, R.PAUSED, R.FAILED, R.CANCELLED, R.INTERRUPTED, R.COMPLETED}
    ),
    R.PAUSED: frozenset({R.QUEUED, R.RUNNING, R.CANCELLED, R.FAILED}),
    # Terminal states can be re-opened by an explicit human action
    # (follow-up instruction, resume or retry), which re-queues the run.
    R.COMPLETED: frozenset({R.QUEUED}),
    R.FAILED: frozenset({R.QUEUED, R.CANCELLED}),
    R.INTERRUPTED: frozenset({R.QUEUED, R.CANCELLED}),
    R.CANCELLED: frozenset(),
}


def ensure_run_transition(current: RunStatus, target: RunStatus) -> None:
    if current == target:
        return
    if target not in RUN_TRANSITIONS[current]:
        raise InvalidTransition("Run", current, target)


# --- Subtasks ----------------------------------------------------------------
S = SubtaskStatus
# Approval steps go straight from waiting to `waiting_approval`, and from there to
# `completed` (approved) or `rejected`.
SUBTASK_TRANSITIONS: dict[SubtaskStatus, frozenset[SubtaskStatus]] = {
    S.PENDING: frozenset(
        {S.WAITING_DEPENDENCY, S.RUNNING, S.WAITING_APPROVAL, S.SKIPPED, S.CANCELLED}
    ),
    S.WAITING_DEPENDENCY: frozenset(
        {S.PENDING, S.RUNNING, S.WAITING_APPROVAL, S.SKIPPED, S.CANCELLED}
    ),
    S.RUNNING: frozenset(
        {S.WAITING_APPROVAL, S.COMPLETED, S.FAILED, S.CONFLICT, S.CANCELLED, S.PENDING}
    ),
    S.WAITING_APPROVAL: frozenset(
        {S.RUNNING, S.COMPLETED, S.REJECTED, S.FAILED, S.CANCELLED, S.PENDING}
    ),
    S.COMPLETED: frozenset(),
    S.FAILED: frozenset({S.PENDING, S.SKIPPED}),
    S.CONFLICT: frozenset({S.PENDING, S.SKIPPED}),
    S.SKIPPED: frozenset({S.PENDING}),
    S.CANCELLED: frozenset({S.PENDING}),
    S.REJECTED: frozenset({S.PENDING}),
}


def ensure_subtask_transition(current: SubtaskStatus, target: SubtaskStatus) -> None:
    if current == target:
        return
    if target not in SUBTASK_TRANSITIONS[current]:
        raise InvalidTransition("Subtask", current, target)
