"""为 AgentRun 增加可恢复的工作流版本标识。

Revision ID: 20260724_0008
Revises: 20260724_0007
Create Date: 2026-07-24
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260724_0008"
down_revision: str | None = "20260724_0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """旧运行默认属于独立审批闸门，新运行会显式选择完整邮件工作流。"""

    op.add_column(
        "agent_runs",
        sa.Column(
            "workflow_name",
            sa.Enum(
                "approval_gate_v1",
                "mail_processing_v1",
                name="agent_workflow_name",
                native_enum=False,
                create_constraint=True,
            ),
            server_default="approval_gate_v1",
            nullable=False,
        ),
    )


def downgrade() -> None:
    """移除工作流版本标识。"""

    op.drop_column("agent_runs", "workflow_name")
