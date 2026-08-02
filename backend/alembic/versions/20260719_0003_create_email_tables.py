"""创建邮件线程与邮件消息表。

Revision ID: 20260719_0003
Revises: 20260719_0002
Create Date: 2026-07-19 03:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260719_0003"
down_revision: str | Sequence[str] | None = "20260719_0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """先创建邮件线程，再创建依赖线程的邮件消息。"""

    op.create_table(
        "email_threads",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column("external_id", sa.String(length=255), nullable=False),
        sa.Column("subject", sa.String(length=500), nullable=False),
        sa.Column(
            "participants",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "status",
            sa.Enum(
                "pending",
                "processing",
                "processed",
                "failed",
                name="email_thread_status",
                native_enum=False,
                create_constraint=True,
            ),
            server_default="pending",
            nullable=False,
        ),
        sa.Column("last_message_at", sa.DateTime(timezone=True), nullable=False),
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
            ["user_id"],
            ["users.id"],
            name=op.f("fk_email_threads_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_email_threads")),
        sa.UniqueConstraint(
            "id",
            "user_id",
            name="uq_email_threads_id_user",
        ),
        sa.UniqueConstraint(
            "user_id",
            "provider",
            "external_id",
            name="uq_email_threads_user_provider_external",
        ),
    )
    op.create_index(
        "ix_email_threads_user_status",
        "email_threads",
        ["user_id", "status"],
        unique=False,
    )
    op.create_index(
        "ix_email_threads_user_updated_at",
        "email_threads",
        ["user_id", "updated_at"],
        unique=False,
    )

    op.create_table(
        "email_messages",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("thread_id", sa.Uuid(), nullable=False),
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column("external_id", sa.String(length=255), nullable=False),
        sa.Column("subject", sa.String(length=500), nullable=False),
        sa.Column("sender", sa.String(length=320), nullable=False),
        sa.Column(
            "recipients",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "cc",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column("body_text", sa.Text(), nullable=False),
        sa.Column(
            "direction",
            sa.Enum(
                "inbound",
                "outbound",
                "draft",
                name="email_direction",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=False,
        ),
        sa.Column(
            "headers",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=False),
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
            ["thread_id", "user_id"],
            ["email_threads.id", "email_threads.user_id"],
            name="fk_email_messages_thread_user_email_threads",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_email_messages_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_email_messages")),
        sa.UniqueConstraint(
            "user_id",
            "provider",
            "external_id",
            name="uq_email_messages_user_provider_external",
        ),
    )
    op.create_index(
        "ix_email_messages_thread_sent_at",
        "email_messages",
        ["thread_id", "sent_at"],
        unique=False,
    )
    op.create_index(
        "ix_email_messages_user_sent_at",
        "email_messages",
        ["user_id", "sent_at"],
        unique=False,
    )


def downgrade() -> None:
    """按依赖关系的反方向删除邮件消息与邮件线程。"""

    op.drop_index("ix_email_messages_user_sent_at", table_name="email_messages")
    op.drop_index("ix_email_messages_thread_sent_at", table_name="email_messages")
    op.drop_table("email_messages")
    op.drop_index("ix_email_threads_user_updated_at", table_name="email_threads")
    op.drop_index("ix_email_threads_user_status", table_name="email_threads")
    op.drop_table("email_threads")
