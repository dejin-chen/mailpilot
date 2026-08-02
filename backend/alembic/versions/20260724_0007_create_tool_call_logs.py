"""创建 MCP 工具调用日志表。

Revision ID: 20260724_0007
Revises: 20260724_0006
Create Date: 2026-07-24 08:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260724_0007"
down_revision: str | Sequence[str] | None = "20260724_0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """创建带用户隔离和幂等唯一约束的工具调用日志。"""

    op.create_table(
        "tool_call_logs",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("agent_run_id", sa.Uuid(), nullable=False),
        sa.Column("approval_request_id", sa.Uuid(), nullable=False),
        sa.Column("tool_name", sa.String(length=100), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "running",
                "succeeded",
                "failed",
                "uncertain",
                name="tool_call_status",
                native_enum=False,
                create_constraint=True,
            ),
            server_default="running",
            nullable=False,
        ),
        sa.Column(
            "arguments",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column(
            "result",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
        sa.Column("error_code", sa.String(length=100), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("idempotency_key", sa.String(length=255), nullable=False),
        sa.Column("request_id", sa.String(length=255), nullable=True),
        sa.Column("attempt_count", sa.Integer(), server_default="1", nullable=False),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
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
            name="fk_tool_call_logs_agent_run_user_agent_runs",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["approval_request_id", "user_id"],
            ["approval_requests.id", "approval_requests.user_id"],
            name="fk_tool_call_logs_approval_user_approval_requests",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_tool_call_logs_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_tool_call_logs")),
        sa.UniqueConstraint(
            "user_id",
            "idempotency_key",
            name="uq_tool_call_logs_user_idempotency_key",
        ),
    )
    op.create_index(
        "ix_tool_call_logs_agent_run_created",
        "tool_call_logs",
        ["agent_run_id", "created_at"],
        unique=False,
    )
    op.create_index(
        "ix_tool_call_logs_approval_created",
        "tool_call_logs",
        ["approval_request_id", "created_at"],
        unique=False,
    )
    op.create_index(
        "ix_tool_call_logs_user_status_created",
        "tool_call_logs",
        ["user_id", "status", "created_at"],
        unique=False,
    )


def downgrade() -> None:
    """删除工具调用日志表。"""

    op.drop_index("ix_tool_call_logs_user_status_created", table_name="tool_call_logs")
    op.drop_index("ix_tool_call_logs_approval_created", table_name="tool_call_logs")
    op.drop_index("ix_tool_call_logs_agent_run_created", table_name="tool_call_logs")
    op.drop_table("tool_call_logs")
