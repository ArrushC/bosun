"""Typed HTTP API schemas (also the source of the frontend's generated TypeScript types)."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from bosun.domain.enums import ExecutionMode, TaskKind, TaskPriority, TaskStatus


class Schema(BaseModel):
    # Response fields with defaults are always present: mark them required in the
    # OpenAPI output schemas so generated client types are precise.
    model_config = ConfigDict(json_schema_serialization_defaults_required=True)


class ORM(Schema):
    model_config = ConfigDict(
        from_attributes=True, json_schema_serialization_defaults_required=True
    )


# -- meta ----------------------------------------------------------------------------
class SandboxSupport(Schema):
    platform: str
    supported: bool
    missing: list[str]
    # Running in a container: shell commands are confined to it and its mounts.
    container: bool = False


class MetaOut(Schema):
    version: str
    data_dir: str
    initial_workspace: str
    app_root: str
    backends: list[str]
    github_write_enabled: bool
    sandbox: SandboxSupport


class DirEntry(Schema):
    name: str
    path: str
    is_git: bool


class BrowseOut(Schema):
    path: str
    parent: str | None
    entries: list[DirEntry]


# -- workspaces ------------------------------------------------------------------------
class WorkspaceOut(ORM):
    id: str
    name: str
    path: str
    is_git: bool
    default_branch: str | None
    status: str
    trusted: bool
    scan_settings: dict[str, Any]
    last_scanned_at: datetime | None
    last_scan_summary: dict[str, Any] | None
    is_demo: bool
    created_at: datetime
    open_items: int = 0
    task_count: int = 0


class WorkspaceCreate(Schema):
    path: str
    name: str | None = None
    trusted: bool = False
    scan: bool = True


class WorkspaceUpdate(Schema):
    name: str | None = None
    trusted: bool | None = None
    scan_settings: dict[str, Any] | None = None


# -- sources --------------------------------------------------------------------------
class SourceRecordOut(ORM):
    id: str
    kind: str
    scope: str
    workspace_id: str | None
    connection_id: str | None
    title: str
    body: str | None
    url: str | None
    path: str | None
    line: int | None
    context: str | None
    tag: str | None
    repo: str | None
    number: int | None
    author: str | None
    labels: list[str]
    meta: dict[str, Any]
    state: str
    dismissed: bool
    first_seen_at: datetime
    last_seen_at: datetime
    external_updated_at: datetime | None
    is_demo: bool
    task_ids: list[str] = Field(default_factory=list)


class SourceRecordPage(Schema):
    items: list[SourceRecordOut]
    total: int
    counts: dict[str, int]


class ImportRequest(Schema):
    record_ids: list[str] = Field(min_length=1, max_length=500)
    status: TaskStatus = TaskStatus.BACKLOG
    execution_mode: ExecutionMode = ExecutionMode.SINGLE
    workspace_id: str | None = None


class DismissRequest(Schema):
    record_ids: list[str] = Field(min_length=1)
    dismissed: bool = True


# -- connections / GitHub ----------------------------------------------------------------
class GitHubFiltersIn(Schema):
    state: Literal["open", "all"] = "open"
    labels: list[str] = Field(default_factory=list)
    milestone: str | None = None
    author: str | None = None
    assignee: str | None = None
    include_issues: bool = True
    include_prs: bool = True


class ConnectionCreate(Schema):
    name: str | None = None
    repos: list[str] = Field(min_length=1)
    filters: GitHubFiltersIn = Field(default_factory=GitHubFiltersIn)
    sync_interval_minutes: int = Field(default=0, ge=0, le=1440)
    workspace_id: str | None = None
    sync_now: bool = True


class ConnectionUpdate(Schema):
    name: str | None = None
    repos: list[str] | None = None
    filters: GitHubFiltersIn | None = None
    sync_interval_minutes: int | None = Field(default=None, ge=0, le=1440)
    workspace_id: str | None = None


class ConnectionOut(ORM):
    id: str
    kind: str
    name: str
    config: dict[str, Any]
    status: str
    last_synced_at: datetime | None
    last_error: str | None
    last_sync_summary: dict[str, Any] | None
    rate_limit_reset_at: datetime | None
    workspace_id: str | None
    created_at: datetime
    open_items: int = 0


class GitHubRepoOut(Schema):
    full_name: str
    description: str | None
    private: bool
    open_issues_count: int
    html_url: str
    pushed_at: str | None
    default_branch: str | None


# -- tasks ----------------------------------------------------------------------------
class SourceRef(ORM):
    id: str
    kind: str
    title: str
    url: str | None
    path: str | None
    line: int | None
    repo: str | None
    number: int | None
    state: str


class RunBrief(Schema):
    id: str
    number: int
    status: str
    mode: str
    strategy: str | None
    backend: str
    started_at: datetime | None
    ended_at: datetime | None
    error_kind: str | None
    sessions: int = 0
    active_sessions: int = 0
    subtasks: int = 0
    subtasks_done: int = 0
    pending_approvals: int = 0
    agents: list[dict[str, Any]] = Field(default_factory=list)
    integration_status: str | None = None


class TaskOut(ORM):
    id: str
    key: str
    number: int
    title: str
    description: str
    status: str
    priority: str
    kind: str
    labels: list[str]
    workspace_id: str | None
    workspace_name: str | None = None
    acceptance_criteria: str
    agent_instructions: str
    execution_mode: str
    attachments: list[dict[str, Any]]
    position: float
    source_state: str | None
    last_activity_at: datetime | None
    last_activity: str | None
    created_at: datetime
    updated_at: datetime
    completed_at: datetime | None
    archived: bool
    is_demo: bool
    sources: list[SourceRef] = Field(default_factory=list)
    depends_on: list[str] = Field(default_factory=list)
    open_dependencies: int = 0
    latest_run: RunBrief | None = None


class ActivityOut(ORM):
    id: str
    kind: str
    actor: str
    message: str
    data: dict[str, Any]
    run_id: str | None
    created_at: datetime


class TaskRef(Schema):
    id: str
    key: str
    title: str
    status: str


class TaskDetail(TaskOut):
    activity: list[ActivityOut] = Field(default_factory=list)
    runs: list[RunBrief] = Field(default_factory=list)
    dependencies: list[TaskRef] = Field(default_factory=list)
    dependents: list[TaskRef] = Field(default_factory=list)


class Attachment(Schema):
    type: Literal["file", "url", "note"] = "url"
    value: str
    label: str | None = None


class TaskCreate(Schema):
    title: str = Field(min_length=1, max_length=500)
    description: str = ""
    status: TaskStatus = TaskStatus.BACKLOG
    priority: TaskPriority = TaskPriority.NONE
    kind: TaskKind = TaskKind.IMPLEMENTATION
    labels: list[str] = Field(default_factory=list)
    workspace_id: str | None = None
    acceptance_criteria: str = ""
    agent_instructions: str = ""
    execution_mode: ExecutionMode = ExecutionMode.SINGLE
    attachments: list[Attachment] = Field(default_factory=list)
    depends_on: list[str] = Field(default_factory=list)


class TaskUpdate(Schema):
    title: str | None = Field(default=None, min_length=1, max_length=500)
    description: str | None = None
    priority: TaskPriority | None = None
    kind: TaskKind | None = None
    labels: list[str] | None = None
    workspace_id: str | None = None
    acceptance_criteria: str | None = None
    agent_instructions: str | None = None
    execution_mode: ExecutionMode | None = None
    attachments: list[Attachment] | None = None
    archived: bool | None = None


class TaskMove(Schema):
    status: TaskStatus
    before_id: str | None = None
    after_id: str | None = None


class DependencyIn(Schema):
    depends_on_id: str


class CommentIn(Schema):
    message: str = Field(min_length=1, max_length=10_000)


# -- runs -----------------------------------------------------------------------------
class SessionOut(ORM):
    id: str
    run_id: str
    subtask_id: str | None
    parent_session_id: str | None
    kind: str
    role: str
    name: str
    status: str
    sdk_session_id: str | None
    model: str | None
    cwd: str | None
    branch: str | None
    prompt: str | None
    result: str | None
    error: str | None
    error_kind: str | None
    num_turns: int
    turn_count: int
    usage: dict[str, Any]
    duration_ms: int | None
    api_key_source: str | None
    attempt: int
    created_at: datetime
    started_at: datetime | None
    ended_at: datetime | None
    live: bool = False


class SubtaskOut(ORM):
    id: str
    key: str
    position: int
    title: str
    role: str
    instructions: str
    depends_on: list[str]
    modifies_code: bool
    status: str
    attempts: int
    max_attempts: int
    report: str | None
    error: str | None
    session_id: str | None
    branch: str | None
    merge_commit: str | None


class ArtifactOut(ORM):
    id: str
    session_id: str | None
    kind: str
    title: str
    path: str | None
    data: dict[str, Any]
    created_at: datetime


class ApprovalOut(ORM):
    id: str
    run_id: str
    task_id: str | None
    session_id: str | None
    subtask_id: str | None
    kind: str
    status: str
    title: str
    description: str | None
    tool_name: str | None
    tool_use_id: str | None
    tool_input: dict[str, Any] | None
    data: dict[str, Any]
    resolution: dict[str, Any] | None
    created_at: datetime
    resolved_at: datetime | None
    expires_at: datetime | None
    task_key: str | None = None
    task_title: str | None = None


class RunOut(ORM):
    id: str
    task_id: str
    task_key: str | None = None
    task_title: str | None = None
    number: int
    mode: str
    strategy: str | None
    status: str
    status_reason: str | None
    backend: str
    provider: str
    model: str | None
    workspace_path: str | None
    isolation: str
    worktree_path: str | None
    branch: str | None
    base_branch: str | None
    base_commit: str | None
    summary: str | None
    error: str | None
    error_kind: str | None
    usage: dict[str, Any]
    cost_usd: float | None
    integration_status: str
    created_at: datetime
    started_at: datetime | None
    ended_at: datetime | None
    is_demo: bool
    active: bool = False
    config: dict[str, Any] = Field(default_factory=dict)
    sessions: list[SessionOut] = Field(default_factory=list)
    subtasks: list[SubtaskOut] = Field(default_factory=list)
    artifacts: list[ArtifactOut] = Field(default_factory=list)
    approvals: list[ApprovalOut] = Field(default_factory=list)


class RunListItem(Schema):
    id: str
    task_id: str
    task_key: str
    task_title: str
    number: int
    mode: str
    strategy: str | None
    status: str
    backend: str
    provider: str
    model: str | None
    created_at: datetime
    started_at: datetime | None
    ended_at: datetime | None
    duration_ms: int | None
    sessions: int
    roles: list[str]
    usage: dict[str, Any]
    cost_usd: float | None
    error_kind: str | None
    is_demo: bool
    integration_status: str


class RunCreate(Schema):
    mode: ExecutionMode | None = None
    role: str | None = None
    team_roles: list[str] | None = None
    backend: Literal["claude", "demo"] | None = None
    model: str | None = None
    effort: Literal["low", "medium", "high", "xhigh", "max"] | None = None
    max_turns: int | None = Field(default=None, ge=1, le=500)
    timeout_minutes: int | None = Field(default=None, ge=1, le=600)
    require_plan_approval: bool | None = None
    allow_subagents: bool | None = None
    max_team_size: int | None = Field(default=None, ge=1, le=8)
    instructions: str | None = Field(default=None, max_length=20_000)
    demo_scenario: dict[str, Any] = Field(default_factory=dict)


class RunPreview(Schema):
    can_start: bool
    errors: list[str]
    warnings: list[str]
    config: dict[str, Any] | None
    scope: dict[str, Any]
    workspace: dict[str, Any] = Field(default_factory=dict)
    provider: dict[str, Any] = Field(default_factory=dict)


class MessageIn(Schema):
    text: str = Field(min_length=1, max_length=20_000)
    session_id: str | None = None
    interrupt: bool = False


class IntegrateIn(Schema):
    action: Literal["merge", "discard"]
    confirm: bool = False


class SubtaskAction(Schema):
    action: Literal["retry", "skip"]


class ApprovalResolve(Schema):
    decision: Literal["approve", "deny"]
    message: str | None = Field(default=None, max_length=4000)
    remember: bool = False


class DiffOut(Schema):
    available: bool
    reason: str | None = None
    branch: str | None = None
    base_branch: str | None = None
    base_commit: str | None = None
    files: list[dict[str, Any]] = Field(default_factory=list)
    patch: str = ""
    truncated: bool = False
    commits: list[dict[str, Any]] = Field(default_factory=list)
    additions: int = 0
    deletions: int = 0


class EventOut(Schema):
    seq: int
    id: str
    run_id: str
    task_id: str | None
    session_id: str | None
    agent_id: str | None
    parent_agent_id: str | None
    type: str
    status: str | None
    ts: datetime
    data: dict[str, Any]


class EventPage(Schema):
    events: list[EventOut]
    last_seq: int
    has_more: bool


# -- settings / roles --------------------------------------------------------------------
class RoleOut(ORM):
    id: str
    key: str
    name: str
    description: str
    system_prompt: str
    permission_profile: str
    model: str | None
    effort: str | None
    max_turns: int | None
    extra_allowed_tools: list[str]
    color: str
    enabled: bool
    builtin: bool
    is_coordinator: bool


class RoleIn(Schema):
    key: str = Field(pattern=r"^[a-z][a-z0-9_-]{1,31}$")
    name: str = Field(min_length=1, max_length=60)
    description: str = ""
    system_prompt: str = ""
    permission_profile: Literal["read_only", "edit", "edit_and_test", "trusted_shell"] = "read_only"
    model: str | None = None
    effort: Literal["low", "medium", "high", "xhigh", "max"] | None = None
    max_turns: int | None = Field(default=None, ge=1, le=500)
    extra_allowed_tools: list[str] = Field(default_factory=list)
    color: str = "violet"
    enabled: bool = True


class RoleUpdate(Schema):
    name: str | None = None
    description: str | None = None
    system_prompt: str | None = None
    permission_profile: Literal["read_only", "edit", "edit_and_test", "trusted_shell"] | None = None
    model: str | None = None
    effort: Literal["low", "medium", "high", "xhigh", "max"] | None = None
    max_turns: int | None = Field(default=None, ge=1, le=500)
    extra_allowed_tools: list[str] | None = None
    color: str | None = None
    enabled: bool | None = None


class ProviderStatusOut(Schema):
    provider: str
    ready: bool
    summary: str
    details: dict[str, Any]
    policy_note: str
    usage: dict[str, Any] = Field(default_factory=dict)


# -- dashboard / graph ------------------------------------------------------------------
class DashboardOut(Schema):
    counts: dict[str, int]
    tasks_by_status: dict[str, int]
    runs_by_status: dict[str, int]
    active_runs: list[RunListItem]
    recent_runs: list[RunListItem]
    pending_approvals: list[ApprovalOut]
    workspaces: list[WorkspaceOut]
    activity: list[dict[str, Any]]
    throughput: list[dict[str, Any]]
    usage: dict[str, Any]


class OverviewGraph(Schema):
    nodes: list[dict[str, Any]]
    edges: list[dict[str, Any]]
