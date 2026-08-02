"""创建 AgentRun、审批与审计表。

Revision ID: 20260724_0006
Revises: 20260720_0005
Create Date: 2026-07-24 01:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260724_0006"
down_revision: str | Sequence[str] | None = "20260720_0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """按外键依赖顺序创建运行、审批和不可变审计表。"""

    op.create_table(
        "agent_runs",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("email_thread_id", sa.Uuid(), nullable=False),
        sa.Column("graph_thread_id", sa.String(length=255), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "pending",
                "running",
                "waiting_approval",
                "completed",
                "failed",
                "cancelled",
                name="agent_run_status",
                native_enum=False,
                create_constraint=True,
            ),
            server_default="pending",
            nullable=False,
        ),
        sa.Column("current_node", sa.String(length=100), server_default="start", nullable=False),
        sa.Column(
            "result",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("error_code", sa.String(length=100), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("input_tokens", sa.Integer(), server_default="0", nullable=False),
        sa.Column("output_tokens", sa.Integer(), server_default="0", nullable=False),
        sa.Column("total_tokens", sa.Integer(), server_default="0", nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
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
            ["email_thread_id", "user_id"],
            ["email_threads.id", "email_threads.user_id"],
            name="fk_agent_runs_email_thread_user_email_threads",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_agent_runs_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_agent_runs")),
        sa.UniqueConstraint("graph_thread_id", name="uq_agent_runs_graph_thread_id"),
        sa.UniqueConstraint("id", "user_id", name="uq_agent_runs_id_user"),
    )
    op.create_index(
        "ix_agent_runs_user_email_thread",
        "agent_runs",
        ["user_id", "email_thread_id"],
        unique=False,
    )
    op.create_index(
        "ix_agent_runs_user_status_created",
        "agent_runs",
        ["user_id", "status", "created_at"],
        unique=False,
    )

    op.create_table(
        "approval_requests",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("agent_run_id", sa.Uuid(), nullable=False),
        sa.Column(
            "action",
            sa.Enum(
                "send_email",
                "create_event",
                "reschedule_event",
                "cancel_event",
                name="approval_action",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=False,
        ),
        sa.Column(
            "status",
            sa.Enum(
                "pending",
                "approved",
                "rejected",
                "feedback_requested",
                "executing",
                "executed",
                "execution_failed",
                name="approval_status",
                native_enum=False,
                create_constraint=True,
            ),
            server_default="pending",
            nullable=False,
        ),
        sa.Column(
            "proposed_arguments",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column(
            "modified_arguments",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
        sa.Column("feedback", sa.Text(), nullable=True),
        sa.Column("idempotency_key", sa.String(length=255), nullable=False),
        sa.Column("version", sa.Integer(), server_default="1", nullable=False),
        sa.Column("decided_by_user_id", sa.Uuid(), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("executed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "execution_result",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
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
        sa.CheckConstraint(
            "version >= 1",
            name=op.f("ck_approval_requests_positive_version"),
        ),
        sa.ForeignKeyConstraint(
            ["agent_run_id", "user_id"],
            ["agent_runs.id", "agent_runs.user_id"],
            name="fk_approval_requests_agent_run_user_agent_runs",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["decided_by_user_id"],
            ["users.id"],
            name=op.f("fk_approval_requests_decided_by_user_id_users"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_approval_requests_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_approval_requests")),
        sa.UniqueConstraint(
            "agent_run_id",
            "action",
            "version",
            name="uq_approval_requests_run_action_version",
        ),
        sa.UniqueConstraint("id", "user_id", name="uq_approval_requests_id_user"),
        sa.UniqueConstraint(
            "user_id",
            "idempotency_key",
            name="uq_approval_requests_user_idempotency_key",
        ),
    )
    op.create_index(
        "ix_approval_requests_agent_run",
        "approval_requests",
        ["agent_run_id", "created_at"],
        unique=False,
    )
    op.create_index(
        "ix_approval_requests_user_status_created",
        "approval_requests",
        ["user_id", "status", "created_at"],
        unique=False,
    )

    op.create_table(
        "audit_logs",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("actor_user_id", sa.Uuid(), nullable=True),
        sa.Column("agent_run_id", sa.Uuid(), nullable=True),
        sa.Column("approval_request_id", sa.Uuid(), nullable=True),
        sa.Column("action", sa.String(length=100), nullable=False),
        sa.Column("resource_type", sa.String(length=100), nullable=False),
        sa.Column("resource_id", sa.Uuid(), nullable=True),
        sa.Column("request_id", sa.String(length=255), nullable=True),
        sa.Column(
            "details",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["actor_user_id"],
            ["users.id"],
            name=op.f("fk_audit_logs_actor_user_id_users"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["agent_run_id"],
            ["agent_runs.id"],
            name=op.f("fk_audit_logs_agent_run_id_agent_runs"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["approval_request_id"],
            ["approval_requests.id"],
            name=op.f("fk_audit_logs_approval_request_id_approval_requests"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_audit_logs_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_audit_logs")),
    )
    op.create_index(
        "ix_audit_logs_agent_run",
        "audit_logs",
        ["agent_run_id", "created_at"],
        unique=False,
    )
    op.create_index(
        "ix_audit_logs_approval",
        "audit_logs",
        ["approval_request_id", "created_at"],
        unique=False,
    )
    op.create_index(
        "ix_audit_logs_user_created",
        "audit_logs",
        ["user_id", "created_at"],
        unique=False,
    )


def downgrade() -> None:
    """按依赖关系反向删除审计、审批和运行表。"""

    op.drop_index("ix_audit_logs_user_created", table_name="audit_logs")
    op.drop_index("ix_audit_logs_approval", table_name="audit_logs")
    op.drop_index("ix_audit_logs_agent_run", table_name="audit_logs")
    op.drop_table("audit_logs")
    op.drop_index(
        "ix_approval_requests_user_status_created",
        table_name="approval_requests",
    )
    op.drop_index("ix_approval_requests_agent_run", table_name="approval_requests")
    op.drop_table("approval_requests")
    op.drop_index("ix_agent_runs_user_status_created", table_name="agent_runs")
    op.drop_index("ix_agent_runs_user_email_thread", table_name="agent_runs")
    op.drop_table("agent_runs")
