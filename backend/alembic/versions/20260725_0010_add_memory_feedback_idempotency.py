"""为审批反馈生成的记忆版本增加来源幂等约束。

Revision ID: 20260725_0010
Revises: 20260724_0009
Create Date: 2026-07-25
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20260725_0010"
down_revision: str | None = "20260724_0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """同一用户的同一来源只允许创建一个记忆版本。"""

    op.create_unique_constraint(
        "uq_memory_profile_versions_user_source_reference",
        "memory_profile_versions",
        [
            "user_id",
            "source_type",
            "source_reference_type",
            "source_reference_id",
        ],
    )


def downgrade() -> None:
    """移除审批反馈来源幂等约束。"""

    op.drop_constraint(
        "uq_memory_profile_versions_user_source_reference",
        "memory_profile_versions",
        type_="unique",
    )
