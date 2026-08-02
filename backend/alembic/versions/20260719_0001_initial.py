"""初始化数据库迁移基线。

Revision ID: 20260719_0001
Revises:
Create Date: 2026-07-19 00:00:00
"""

from collections.abc import Sequence

revision: str = "20260719_0001"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """建立第 1 阶段空迁移基线；业务表从第 2 阶段开始加入。"""


def downgrade() -> None:
    """空基线没有需要回滚的业务对象。"""
