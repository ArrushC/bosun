from __future__ import annotations

from enum import StrEnum


class TaskStatus(StrEnum):
    BACKLOG = "backlog"
    READY = "ready"
    IN_PROGRESS = "in_progress"
    REVIEW = "review"
    BLOCKED = "blocked"
    DONE = "done"


class TaskPriority(StrEnum):
    URGENT = "urgent"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    NONE = "none"


class TaskKind(StrEnum):
    IMPLEMENTATION = "implementation"
    BUGFIX = "bugfix"
    INVESTIGATION = "investigation"
    REVIEW = "review"
    TESTING = "testing"
    REFACTOR = "refactor"
    CHORE = "chore"


class ExecutionMode(StrEnum):
    SINGLE = "single"
    TEAM = "team"
    AUTO = "auto"


class RunStatus(StrEnum):
    QUEUED = "queued"
    PREPARING = "preparing"
    PLANNING = "planning"
    RUNNING = "running"
    WAITING_APPROVAL = "waiting_approval"
    PAUSED = "paused"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    INTERRUPTED = "interrupted"


ACTIVE_RUN_STATUSES = frozenset(
    {
        RunStatus.QUEUED,
        RunStatus.PREPARING,
        RunStatus.PLANNING,
        RunStatus.RUNNING,
        RunStatus.WAITING_APPROVAL,
    }
)
TERMINAL_RUN_STATUSES = frozenset(
    {RunStatus.COMPLETED, RunStatus.FAILED, RunStatus.CANCELLED, RunStatus.INTERRUPTED}
)


class SessionStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    WAITING_APPROVAL = "waiting_approval"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    INTERRUPTED = "interrupted"


TERMINAL_SESSION_STATUSES = frozenset(
    {
        SessionStatus.COMPLETED,
        SessionStatus.FAILED,
        SessionStatus.CANCELLED,
        SessionStatus.INTERRUPTED,
    }
)


class SessionKind(StrEnum):
    AGENT = "agent"
    COORDINATOR = "coordinator"


class SubtaskStatus(StrEnum):
    PENDING = "pending"
    WAITING_DEPENDENCY = "waiting_dependency"
    RUNNING = "running"
    WAITING_APPROVAL = "waiting_approval"
    COMPLETED = "completed"
    FAILED = "failed"
    CONFLICT = "conflict"
    SKIPPED = "skipped"
    CANCELLED = "cancelled"


TERMINAL_SUBTASK_STATUSES = frozenset(
    {
        SubtaskStatus.COMPLETED,
        SubtaskStatus.FAILED,
        SubtaskStatus.CONFLICT,
        SubtaskStatus.SKIPPED,
        SubtaskStatus.CANCELLED,
    }
)


class ApprovalKind(StrEnum):
    TOOL_PERMISSION = "tool_permission"
    PLAN = "plan"
    INTEGRATION = "integration"


class ApprovalStatus(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    DENIED = "denied"
    EXPIRED = "expired"
    CANCELLED = "cancelled"


class SourceKind(StrEnum):
    CODE_TODO = "code_todo"
    MARKDOWN_TASK = "markdown_task"
    TODO_FILE = "todo_file"
    GITHUB_ISSUE = "github_issue"
    GITHUB_PR = "github_pr"


class SourceRecordState(StrEnum):
    OPEN = "open"
    CLOSED = "closed"
    MERGED = "merged"
    RESOLVED = "resolved"  # disappeared from the scanned source
    DELETED = "deleted"
    DISMISSED = "dismissed"


class ConnectionStatus(StrEnum):
    IDLE = "idle"
    SYNCING = "syncing"
    OK = "ok"
    ERROR = "error"
    RATE_LIMITED = "rate_limited"


class Provider(StrEnum):
    SUBSCRIPTION = "subscription"
    API_KEY = "api_key"
    VERTEX = "vertex"


class ExecutionBackend(StrEnum):
    CLAUDE = "claude"
    DEMO = "demo"


class PermissionProfile(StrEnum):
    READ_ONLY = "read_only"
    EDIT = "edit"
    EDIT_AND_TEST = "edit_and_test"
    TRUSTED_SHELL = "trusted_shell"


class WorkspaceStatus(StrEnum):
    OK = "ok"
    MISSING = "missing"
