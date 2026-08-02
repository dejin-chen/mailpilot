"""为邮件消息增加幂等键。

Revision ID: 20260720_0005
Revises: 20260719_0004
Create Date: 2026-07-20 01:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260720_0005"
down_revision: str | Sequence[str] | None = "20260719_0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """增加可为空的幂等键；PostgreSQL 允许多行 NULL。"""

    op.add_column(
        "email_messages",
        sa.Column("idempotency_key", sa.String(length=255), nullable=True),
    )
    op.create_unique_constraint(
        "uq_email_messages_user_idempotency_key",
        "email_messages",
        ["user_id", "idempotency_key"],
    )


def downgrade() -> None:
    """删除邮件消息幂等键。"""

    op.drop_constraint(
        "uq_email_messages_user_idempotency_key",
        "email_messages",
        type_="unique",
    )
    op.drop_column("email_messages", "idempotency_key")
