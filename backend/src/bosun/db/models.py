"""Persistent domain model.

Source identity (`SourceRecord`) is deliberately separate from task identity
(`Task`): a task may be linked to several records (a TODO comment *and* the
GitHub issue tracking it), and discovered records can exist without becoming
tasks until a human promotes them.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from bosun.db.base import Base, UTCDateTime
from bosun.domain.enums import (
    ACTIVE_RUN_STATUSES,
    ApprovalStatus,
    ConnectionStatus,
    ExecutionMode,
    RunStatus,
    SessionKind,
    SessionStatus,
    SourceRecordState,
    SubtaskKind,
    SubtaskStatus,
    TaskKind,
    TaskPriority,
    TaskStatus,
    WorkspaceStatus,
)
from bosun.ids import new_id, utcnow

JsonDict = dict[str, Any]


def _id(prefix: str):
    return lambda: new_id(prefix)


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime, default=utcnow, onupdate=utcnow, nullable=False
    )


class Workspace(TimestampMixin, Base):
    __tablename__ = "workspaces"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_id("ws"))
    name: Mapped[str] = mapped_column(String, nullable=False)
    path: Mapped[str] = mapped_column(String, nullable=False, unique=True)
    is_git: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    default_branch: Mapped[str | None] = mapped_column(String)
    status: Mapped[str] = mapped_column(String, default=WorkspaceStatus.OK, nullable=False)
    # Trusted workspaces load project `.claude/` settings and CLAUDE.md natively.
    trusted: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    scan_settings: Mapped[JsonDict] = mapped_column(JSON, default=dict, nullable=False)
    last_scanned_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    last_scan_summary: Mapped[JsonDict | None] = mapped_column(JSON)
    is_demo: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)


class SourceConnection(TimestampMixin, Base):
    __tablename__ = "source_connections"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_id("conn"))
    kind: Mapped[str] = mapped_column(String, nullable=False)  # "github"
    name: Mapped[str] = mapped_column(String, nullable=False)
    config: Mapped[JsonDict] = mapped_column(JSON, default=dict, nullable=False)
    status: Mapped[str] = mapped_column(String, default=ConnectionStatus.IDLE, nullable=False)
    last_synced_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    last_error: Mapped[str | None] = mapped_column(Text)
    last_sync_summary: Mapped[JsonDict | None] = mapped_column(JSON)
    rate_limit_reset_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    # Workspace whose checkout agents use when running tasks imported from here.
    workspace_id: Mapped[str | None] = mapped_column(
        ForeignKey("workspaces.id", ondelete="SET NULL")
    )


class SourceRecord(Base):
    __tablename__ = "source_records"
    __table_args__ = (UniqueConstraint("scope", "external_id", name="uq_source_records_identity"),)

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_id("src"))
    kind: Mapped[str] = mapped_column(String, nullable=False, index=True)
    # Deduplication scope: the workspace id or connection id that discovered it.
    scope: Mapped[str] = mapped_column(String, nullable=False)
    external_id: Mapped[str] = mapped_column(String, nullable=False)
    workspace_id: Mapped[str | None] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), index=True
    )
    connection_id: Mapped[str | None] = mapped_column(
        ForeignKey("source_connections.id", ondelete="CASCADE"), index=True
    )
    title: Mapped[str] = mapped_column(Text, nullable=False)
    body: Mapped[str | None] = mapped_column(Text)
    url: Mapped[str | None] = mapped_column(String)
    path: Mapped[str | None] = mapped_column(String)
    line: Mapped[int | None] = mapped_column(Integer)
    context: Mapped[str | None] = mapped_column(Text)
    tag: Mapped[str | None] = mapped_column(String)
    repo: Mapped[str | None] = mapped_column(String)
    number: Mapped[int | None] = mapped_column(Integer)
    author: Mapped[str | None] = mapped_column(String)
    labels: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    meta: Mapped[JsonDict] = mapped_column(JSON, default=dict, nullable=False)
    state: Mapped[str] = mapped_column(String, default=SourceRecordState.OPEN, nullable=False)
    dismissed: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    content_hash: Mapped[str | None] = mapped_column(String)
    first_seen_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, nullable=False)
    external_updated_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    is_demo: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    tasks: Mapped[list[Task]] = relationship(
        secondary="task_sources", back_populates="sources", viewonly=True
    )


class Task(TimestampMixin, Base):
    __tablename__ = "tasks"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_id("tsk"))
    number: Mapped[int] = mapped_column(Integer, nullable=False, unique=True)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str] = mapped_column(Text, default="", nullable=False)
    status: Mapped[str] = mapped_column(
        String, default=TaskStatus.BACKLOG, nullable=False, index=True
    )
    priority: Mapped[str] = mapped_column(String, default=TaskPriority.NONE, nullable=False)
    kind: Mapped[str] = mapped_column(String, default=TaskKind.IMPLEMENTATION, nullable=False)
    labels: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    workspace_id: Mapped[str | None] = mapped_column(
        ForeignKey("workspaces.id", ondelete="SET NULL"), index=True
    )
    acceptance_criteria: Mapped[str] = mapped_column(Text, default="", nullable=False)
    agent_instructions: Mapped[str] = mapped_column(Text, default="", nullable=False)
    execution_mode: Mapped[str] = mapped_column(
        String, default=ExecutionMode.SINGLE, nullable=False
    )
    attachments: Mapped[list[JsonDict]] = mapped_column(JSON, default=list, nullable=False)
    position: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    source_state: Mapped[str | None] = mapped_column(String)
    last_activity_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    last_activity: Mapped[str | None] = mapped_column(Text)
    completed_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    archived: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_demo: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    sources: Mapped[list[SourceRecord]] = relationship(
        secondary="task_sources", back_populates="tasks"
    )
    workspace: Mapped[Workspace | None] = relationship()

    @property
    def key(self) -> str:
        return f"BOS-{self.number}"


class TaskSourceLink(Base):
    __tablename__ = "task_sources"

    task_id: Mapped[str] = mapped_column(
        ForeignKey("tasks.id", ondelete="CASCADE"), primary_key=True
    )
    source_record_id: Mapped[str] = mapped_column(
        ForeignKey("source_records.id", ondelete="CASCADE"), primary_key=True
    )
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, nullable=False)


class TaskDependency(Base):
    __tablename__ = "task_dependencies"

    task_id: Mapped[str] = mapped_column(
        ForeignKey("tasks.id", ondelete="CASCADE"), primary_key=True
    )
    depends_on_id: Mapped[str] = mapped_column(
        ForeignKey("tasks.id", ondelete="CASCADE"), primary_key=True
    )
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, nullable=False)


class TaskActivity(Base):
    __tablename__ = "task_activity"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_id("act"))
    task_id: Mapped[str] = mapped_column(
        ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False, index=True
    )
    kind: Mapped[str] = mapped_column(String, nullable=False)
    actor: Mapped[str] = mapped_column(String, default="human", nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    data: Mapped[JsonDict] = mapped_column(JSON, default=dict, nullable=False)
    run_id: Mapped[str | None] = mapped_column(String)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, nullable=False)


class AgentRole(TimestampMixin, Base):
    __tablename__ = "agent_roles"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_id("role"))
    key: Mapped[str] = mapped_column(String, nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String, nullable=False)
    description: Mapped[str] = mapped_column(Text, default="", nullable=False)
    system_prompt: Mapped[str] = mapped_column(Text, default="", nullable=False)
    permission_profile: Mapped[str] = mapped_column(String, nullable=False)
    model: Mapped[str | None] = mapped_column(String)
    effort: Mapped[str | None] = mapped_column(String)
    max_turns: Mapped[int | None] = mapped_column(Integer)
    extra_allowed_tools: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    color: Mapped[str] = mapped_column(String, default="violet", nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    builtin: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_coordinator: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)


class Workflow(TimestampMixin, Base):
    """An authored graph of steps (see bosun.workflows.spec): its editable draft."""

    __tablename__ = "workflows"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_id("wf"))
    name: Mapped[str] = mapped_column(String, nullable=False)
    description: Mapped[str] = mapped_column(Text, default="", nullable=False)
    spec: Mapped[JsonDict] = mapped_column(JSON, default=dict, nullable=False)
    # Increases on every saved edit; a save must name the revision it started from.
    revision: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    archived: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    # The template it was created from, if any. Only a label: the draft is a copy.
    template: Mapped[str | None] = mapped_column(String)


class WorkflowVersion(Base):
    """A published, numbered copy of a workflow's draft, with its agents frozen in.

    Versions never change: the database refuses updates and deletes (migration 0003).
    """

    __tablename__ = "workflow_versions"
    __table_args__ = (
        UniqueConstraint("workflow_id", "number", name="uq_workflow_versions_number"),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_id("wfv"))
    workflow_id: Mapped[str] = mapped_column(ForeignKey("workflows.id"), nullable=False)
    number: Mapped[int] = mapped_column(Integer, nullable=False)
    name: Mapped[str] = mapped_column(String, nullable=False)
    description: Mapped[str] = mapped_column(Text, default="", nullable=False)
    spec: Mapped[JsonDict] = mapped_column(JSON, nullable=False)
    # The definitions of the agents its steps use, as they were when it was published.
    agents: Mapped[JsonDict] = mapped_column(JSON, nullable=False)
    note: Mapped[str] = mapped_column(Text, default="", nullable=False)
    # The draft revision it was published from.
    draft_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, nullable=False)


class AgentRun(TimestampMixin, Base):
    __tablename__ = "agent_runs"
    __table_args__ = (
        # At most one active run per task: prevents duplicate execution from
        # double clicks, retried requests or reconnecting clients.
        Index(
            "uq_agent_runs_one_active_per_task",
            "task_id",
            unique=True,
            sqlite_where=text(
                "status IN (" + ", ".join(f"'{s.value}'" for s in sorted(ACTIVE_RUN_STATUSES)) + ")"
            ),
        ),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_id("run"))
    task_id: Mapped[str] = mapped_column(
        ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False, index=True
    )
    number: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    mode: Mapped[str] = mapped_column(String, nullable=False)
    strategy: Mapped[str | None] = mapped_column(String)  # resolved: single | team | workflow
    # The workflow this run executed (its draft is frozen in `config.workflow`).
    workflow_id: Mapped[str | None] = mapped_column(String, index=True)
    # The published version it ran; None for a draft (and for runs that aren't workflows).
    workflow_version: Mapped[int | None] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(
        String, default=RunStatus.QUEUED, nullable=False, index=True
    )
    status_reason: Mapped[str | None] = mapped_column(Text)
    backend: Mapped[str] = mapped_column(String, nullable=False)
    provider: Mapped[str] = mapped_column(String, nullable=False)
    model: Mapped[str | None] = mapped_column(String)
    workspace_id: Mapped[str | None] = mapped_column(
        ForeignKey("workspaces.id", ondelete="SET NULL")
    )
    workspace_path: Mapped[str | None] = mapped_column(String)
    isolation: Mapped[str] = mapped_column(String, default="worktree", nullable=False)
    worktree_path: Mapped[str | None] = mapped_column(String)
    branch: Mapped[str | None] = mapped_column(String)
    base_branch: Mapped[str | None] = mapped_column(String)
    base_commit: Mapped[str | None] = mapped_column(String)
    config: Mapped[JsonDict] = mapped_column(JSON, default=dict, nullable=False)
    summary: Mapped[str | None] = mapped_column(Text)
    error: Mapped[str | None] = mapped_column(Text)
    error_kind: Mapped[str | None] = mapped_column(String)
    usage: Mapped[JsonDict] = mapped_column(JSON, default=dict, nullable=False)
    cost_usd: Mapped[float | None] = mapped_column(Float)
    integration_status: Mapped[str] = mapped_column(String, default="pending", nullable=False)
    idempotency_key: Mapped[str | None] = mapped_column(String, unique=True)
    started_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    ended_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    is_demo: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    task: Mapped[Task] = relationship()
    sessions: Mapped[list[AgentSession]] = relationship(
        back_populates="run", order_by="AgentSession.created_at", cascade="all, delete-orphan"
    )
    subtasks: Mapped[list[Subtask]] = relationship(
        back_populates="run", order_by="Subtask.position", cascade="all, delete-orphan"
    )


class AgentSession(TimestampMixin, Base):
    __tablename__ = "agent_sessions"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_id("ses"))
    run_id: Mapped[str] = mapped_column(
        ForeignKey("agent_runs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    subtask_id: Mapped[str | None] = mapped_column(ForeignKey("subtasks.id", ondelete="SET NULL"))
    parent_session_id: Mapped[str | None] = mapped_column(String)
    kind: Mapped[str] = mapped_column(String, default=SessionKind.AGENT, nullable=False)
    role: Mapped[str] = mapped_column(String, nullable=False)
    name: Mapped[str] = mapped_column(String, nullable=False)
    status: Mapped[str] = mapped_column(String, default=SessionStatus.PENDING, nullable=False)
    sdk_session_id: Mapped[str | None] = mapped_column(String, index=True)
    model: Mapped[str | None] = mapped_column(String)
    cwd: Mapped[str | None] = mapped_column(String)
    branch: Mapped[str | None] = mapped_column(String)
    prompt: Mapped[str | None] = mapped_column(Text)
    result: Mapped[str | None] = mapped_column(Text)
    structured_output: Mapped[JsonDict | None] = mapped_column(JSON)
    error: Mapped[str | None] = mapped_column(Text)
    error_kind: Mapped[str | None] = mapped_column(String)
    num_turns: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    turn_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    usage: Mapped[JsonDict] = mapped_column(JSON, default=dict, nullable=False)
    cost_usd: Mapped[float | None] = mapped_column(Float)
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    api_key_source: Mapped[str | None] = mapped_column(String)
    pid: Mapped[int | None] = mapped_column(Integer)
    attempt: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    ended_at: Mapped[datetime | None] = mapped_column(UTCDateTime)

    run: Mapped[AgentRun] = relationship(back_populates="sessions")


class Subtask(TimestampMixin, Base):
    __tablename__ = "subtasks"
    __table_args__ = (UniqueConstraint("run_id", "key", name="uq_subtasks_run_key"),)

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_id("sub"))
    run_id: Mapped[str] = mapped_column(
        ForeignKey("agent_runs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    key: Mapped[str] = mapped_column(String, nullable=False)
    kind: Mapped[str] = mapped_column(
        String, default=SubtaskKind.AGENT, server_default=SubtaskKind.AGENT.value, nullable=False
    )
    position: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    role: Mapped[str] = mapped_column(String, nullable=False)
    instructions: Mapped[str] = mapped_column(Text, default="", nullable=False)
    depends_on: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    modifies_code: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    status: Mapped[str] = mapped_column(String, default=SubtaskStatus.PENDING, nullable=False)
    attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    max_attempts: Mapped[int] = mapped_column(Integer, default=2, nullable=False)
    report: Mapped[str | None] = mapped_column(Text)
    error: Mapped[str | None] = mapped_column(Text)
    session_id: Mapped[str | None] = mapped_column(String)
    worktree_path: Mapped[str | None] = mapped_column(String)
    branch: Mapped[str | None] = mapped_column(String)
    merge_commit: Mapped[str | None] = mapped_column(String)

    run: Mapped[AgentRun] = relationship(back_populates="subtasks")


class ExecutionEvent(Base):
    """Append-only, ordered log of everything observed during agent execution.

    `seq` is a monotonically increasing integer used for ordering, client
    catch-up after reconnects, and historical replay.
    """

    __tablename__ = "execution_events"

    seq: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    id: Mapped[str] = mapped_column(String, nullable=False, unique=True, default=_id("evt"))
    run_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    task_id: Mapped[str | None] = mapped_column(String, index=True)
    session_id: Mapped[str | None] = mapped_column(String)
    agent_id: Mapped[str | None] = mapped_column(String)
    parent_agent_id: Mapped[str | None] = mapped_column(String)
    type: Mapped[str] = mapped_column(String, nullable=False)
    status: Mapped[str | None] = mapped_column(String)
    ts: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, nullable=False)
    data: Mapped[JsonDict] = mapped_column(JSON, default=dict, nullable=False)


class Artifact(Base):
    __tablename__ = "artifacts"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_id("art"))
    run_id: Mapped[str] = mapped_column(
        ForeignKey("agent_runs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    session_id: Mapped[str | None] = mapped_column(String)
    kind: Mapped[str] = mapped_column(String, nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    path: Mapped[str | None] = mapped_column(String)
    data: Mapped[JsonDict] = mapped_column(JSON, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, nullable=False)


class ApprovalRequest(Base):
    __tablename__ = "approval_requests"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_id("apr"))
    run_id: Mapped[str] = mapped_column(
        ForeignKey("agent_runs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    task_id: Mapped[str | None] = mapped_column(String)
    session_id: Mapped[str | None] = mapped_column(String)
    subtask_id: Mapped[str | None] = mapped_column(String)
    kind: Mapped[str] = mapped_column(String, nullable=False)
    status: Mapped[str] = mapped_column(
        String, default=ApprovalStatus.PENDING, nullable=False, index=True
    )
    title: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    tool_name: Mapped[str | None] = mapped_column(String)
    tool_use_id: Mapped[str | None] = mapped_column(String)
    tool_input: Mapped[JsonDict | None] = mapped_column(JSON)
    data: Mapped[JsonDict] = mapped_column(JSON, default=dict, nullable=False)
    resolution: Mapped[JsonDict | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, nullable=False)
    resolved_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    expires_at: Mapped[datetime | None] = mapped_column(UTCDateTime)


class SettingEntry(Base):
    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(String, primary_key=True)
    value: Mapped[Any] = mapped_column(JSON, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime, default=utcnow, onupdate=utcnow, nullable=False
    )
