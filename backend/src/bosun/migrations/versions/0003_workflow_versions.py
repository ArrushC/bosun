"""workflow versions, approval steps and templates

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-21 21:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

import bosun.db.base

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

IMMUTABLE = "Workflow versions are immutable: publish a new version instead."


def upgrade() -> None:
    op.create_table(
        "workflow_versions",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("workflow_id", sa.String(), nullable=False),
        sa.Column("number", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("spec", sa.JSON(), nullable=False),
        sa.Column("agents", sa.JSON(), nullable=False),
        sa.Column("note", sa.Text(), nullable=False),
        sa.Column("draft_revision", sa.Integer(), nullable=False),
        sa.Column("created_at", bosun.db.base.UTCDateTime(), nullable=False),
        sa.ForeignKeyConstraint(["workflow_id"], ["workflows.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("workflow_id", "number", name="uq_workflow_versions_number"),
    )
    # Published versions are immutable; runs and history rely on it.
    for action in ("UPDATE", "DELETE"):
        op.execute(
            f"CREATE TRIGGER workflow_versions_no_{action.lower()} BEFORE {action} "
            f"ON workflow_versions BEGIN SELECT RAISE(ABORT, '{IMMUTABLE}'); END"
        )
    # Plain ADD COLUMNs: SQLite keeps each table (and its indexes) as it is.
    op.add_column("workflows", sa.Column("template", sa.String(), nullable=True))
    op.add_column("agent_runs", sa.Column("workflow_version", sa.Integer(), nullable=True))
    op.add_column(
        "subtasks",
        sa.Column("kind", sa.String(), server_default="agent", nullable=False),
    )


def downgrade() -> None:
    # Loses every published version, which template a workflow came from, and which
    # steps were approval steps (see docs/VALIDATION.md before rolling back).
    with op.batch_alter_table("subtasks") as batch:
        batch.drop_column("kind")
    with op.batch_alter_table("agent_runs") as batch:
        batch.drop_column("workflow_version")
    with op.batch_alter_table("workflows") as batch:
        batch.drop_column("template")
    op.execute("DROP TRIGGER IF EXISTS workflow_versions_no_update")
    op.execute("DROP TRIGGER IF EXISTS workflow_versions_no_delete")
    op.drop_table("workflow_versions")
