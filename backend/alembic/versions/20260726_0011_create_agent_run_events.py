"""创建 Agent 执行事件时间线表。

Revision ID: 20260726_0011
Revises: 20260725_0010
Create Date: 2026-07-26
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260726_0011"
down_revision: str | None = "20260725_0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """创建带用户隔离和单次运行递增序号的事件表。"""

    op.create_table(
        "agent_run_events",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("agent_run_id", sa.Uuid(), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column(
            "event_type",
            sa.Enum(
                "run_created",
                "run_started",
                "run_resumed",
                "node_completed",
                "approval_required",
                "run_completed",
                "run_failed",
                "run_cancelled",
                name="agent_run_event_type",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=False,
        ),
        sa.Column("node_name", sa.String(length=100), nullable=True),
        sa.Column(
            "payload",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["agent_run_id", "user_id"],
            ["agent_runs.id", "agent_runs.user_id"],
            name="fk_agent_run_events_agent_run_user_agent_runs",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_agent_run_events_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_agent_run_events")),
        sa.UniqueConstraint(
            "agent_run_id",
            "sequence",
            name="uq_agent_run_events_run_sequence",
        ),
    )
    op.create_index(
        "ix_agent_run_events_user_run_sequence",
        "agent_run_events",
        ["user_id", "agent_run_id", "sequence"],
        unique=False,
    )


def downgrade() -> None:
    """删除 Agent 执行事件表。"""

    op.drop_index(
        "ix_agent_run_events_user_run_sequence",
        table_name="agent_run_events",
    )
    op.drop_table("agent_run_events")
