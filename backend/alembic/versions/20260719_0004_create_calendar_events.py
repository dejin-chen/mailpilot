"""创建日历事件表。

Revision ID: 20260719_0004
Revises: 20260719_0003
Create Date: 2026-07-19 04:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260719_0004"
down_revision: str | Sequence[str] | None = "20260719_0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """创建 calendar_events 表、约束和查询索引。"""

    op.create_table(
        "calendar_events",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column("external_id", sa.String(length=255), nullable=False),
        sa.Column("title", sa.String(length=500), nullable=False),
        sa.Column("description", sa.Text(), server_default="", nullable=False),
        sa.Column("start_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("end_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("timezone", sa.String(length=64), nullable=False),
        sa.Column(
            "attendees",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column("location", sa.String(length=500), nullable=True),
        sa.Column("is_all_day", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "tentative",
                "confirmed",
                "cancelled",
                name="calendar_event_status",
                native_enum=False,
                create_constraint=True,
            ),
            server_default="confirmed",
            nullable=False,
        ),
        sa.Column("idempotency_key", sa.String(length=255), nullable=True),
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
            "start_at < end_at",
            name=op.f("ck_calendar_events_valid_time_range"),
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_calendar_events_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_calendar_events")),
        sa.UniqueConstraint(
            "user_id",
            "idempotency_key",
            name="uq_calendar_events_user_idempotency_key",
        ),
        sa.UniqueConstraint(
            "user_id",
            "provider",
            "external_id",
            name="uq_calendar_events_user_provider_external",
        ),
    )
    op.create_index(
        "ix_calendar_events_user_status",
        "calendar_events",
        ["user_id", "status"],
        unique=False,
    )
    op.create_index(
        "ix_calendar_events_user_time",
        "calendar_events",
        ["user_id", "start_at", "end_at"],
        unique=False,
    )


def downgrade() -> None:
    """删除日历事件表。"""

    op.drop_index("ix_calendar_events_user_time", table_name="calendar_events")
    op.drop_index("ix_calendar_events_user_status", table_name="calendar_events")
    op.drop_table("calendar_events")
