"""workflows

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-20 20:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

import bosun.db.base

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "workflows",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("spec", sa.JSON(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("archived", sa.Boolean(), nullable=False),
        sa.Column("created_at", bosun.db.base.UTCDateTime(), nullable=False),
        sa.Column("updated_at", bosun.db.base.UTCDateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    # A plain ADD COLUMN: SQLite keeps the table (and its partial unique index) as is.
    op.add_column("agent_runs", sa.Column("workflow_id", sa.String(), nullable=True))
    op.create_index("ix_agent_runs_workflow_id", "agent_runs", ["workflow_id"])


def downgrade() -> None:
    op.drop_index("ix_agent_runs_workflow_id", table_name="agent_runs")
    with op.batch_alter_table("agent_runs") as batch:
        batch.drop_column("workflow_id")
    op.drop_table("workflows")
