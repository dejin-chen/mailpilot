"""创建长期记忆档案和不可变版本表。

Revision ID: 20260724_0009
Revises: 20260724_0008
Create Date: 2026-07-24
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260724_0009"
down_revision: str | None = "20260724_0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """创建用户隔离、可版本化的长期记忆业务表。"""

    op.create_table(
        "memory_profiles",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column(
            "memory_type",
            sa.Enum(
                "email_style",
                "calendar_preferences",
                "contact",
                name="memory_type",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=False,
        ),
        sa.Column("memory_key", sa.String(length=255), nullable=False),
        sa.Column("current_version", sa.Integer(), server_default="1", nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "current_version >= 1",
            name=op.f("ck_memory_profiles_positive_current_version"),
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_memory_profiles_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_memory_profiles")),
        sa.UniqueConstraint(
            "id",
            "user_id",
            name="uq_memory_profiles_id_user",
        ),
        sa.UniqueConstraint(
            "user_id",
            "memory_type",
            "memory_key",
            name="uq_memory_profiles_user_type_key",
        ),
    )
    op.create_index(
        "ix_memory_profiles_user_type_updated",
        "memory_profiles",
        ["user_id", "memory_type", "updated_at"],
        unique=False,
    )
    op.create_table(
        "memory_profile_versions",
        sa.Column("memory_profile_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column(
            "value",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "source_type",
            sa.Enum(
                "user_api_edit",
                "approval_feedback",
                "draft_modification",
                "calendar_modification",
                name="memory_source_type",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=False,
        ),
        sa.Column("source_reference_type", sa.String(length=100), nullable=True),
        sa.Column("source_reference_id", sa.Uuid(), nullable=True),
        sa.Column("created_by_user_id", sa.Uuid(), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "version >= 1",
            name=op.f("ck_memory_profile_versions_positive_version"),
        ),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"],
            ["users.id"],
            name=op.f("fk_memory_profile_versions_created_by_user_id_users"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["memory_profile_id", "user_id"],
            ["memory_profiles.id", "memory_profiles.user_id"],
            name="fk_memory_profile_versions_profile_user_memory_profiles",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_memory_profile_versions")),
        sa.UniqueConstraint(
            "memory_profile_id",
            "version",
            name="uq_memory_profile_versions_profile_version",
        ),
    )
    op.create_index(
        "ix_memory_profile_versions_user_profile_version",
        "memory_profile_versions",
        ["user_id", "memory_profile_id", "version"],
        unique=False,
    )


def downgrade() -> None:
    """删除长期记忆版本表和档案表。"""

    op.drop_index(
        "ix_memory_profile_versions_user_profile_version",
        table_name="memory_profile_versions",
    )
    op.drop_table("memory_profile_versions")
    op.drop_index(
        "ix_memory_profiles_user_type_updated",
        table_name="memory_profiles",
    )
    op.drop_table("memory_profiles")
