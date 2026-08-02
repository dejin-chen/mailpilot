"""Agent 执行记录 ORM 模型。"""

from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import UUID

from sqlalchemy import (
    DateTime,
    Enum,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.mutable import MutableDict
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.mixins import TimestampMixin, UUIDPrimaryKeyMixin


class AgentRunStatus(StrEnum):
    """一次 AgentRun 的业务生命周期。"""

    PENDING = "pending"
    RUNNING = "running"
    WAITING_APPROVAL = "waiting_approval"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class AgentWorkflowName(StrEnum):
    """用于恢复时选择正确 Graph 结构的稳定工作流标识。"""

    APPROVAL_GATE_V1 = "approval_gate_v1"
    MAIL_PROCESSING_V1 = "mail_processing_v1"


class AgentRun(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """面向 API、审批中心和执行轨迹的一次 Agent 运行。"""

    __tablename__ = "agent_runs"
    __table_args__ = (
        ForeignKeyConstraint(
            ["email_thread_id", "user_id"],
            ["email_threads.id", "email_threads.user_id"],
            ondelete="RESTRICT",
            name="fk_agent_runs_email_thread_user_email_threads",
        ),
        UniqueConstraint("id", "user_id", name="uq_agent_runs_id_user"),
        UniqueConstraint("graph_thread_id", name="uq_agent_runs_graph_thread_id"),
        Index("ix_agent_runs_user_status_created", "user_id", "status", "created_at"),
        Index("ix_agent_runs_user_email_thread", "user_id", "email_thread_id"),
    )

    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    email_thread_id: Mapped[UUID] = mapped_column(nullable=False)
    graph_thread_id: Mapped[str] = mapped_column(String(255), nullable=False)
    workflow_name: Mapped[AgentWorkflowName] = mapped_column(
        Enum(
            AgentWorkflowName,
            name="agent_workflow_name",
            native_enum=False,
            create_constraint=True,
            validate_strings=True,
            values_callable=lambda enum_type: [item.value for item in enum_type],
        ),
        nullable=False,
        default=AgentWorkflowName.APPROVAL_GATE_V1,
        server_default=AgentWorkflowName.APPROVAL_GATE_V1.value,
    )
    status: Mapped[AgentRunStatus] = mapped_column(
        Enum(
            AgentRunStatus,
            name="agent_run_status",
            native_enum=False,
            create_constraint=True,
            validate_strings=True,
            values_callable=lambda enum_type: [item.value for item in enum_type],
        ),
        nullable=False,
        default=AgentRunStatus.PENDING,
        server_default=AgentRunStatus.PENDING.value,
    )
    current_node: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
        default="start",
        server_default="start",
    )
    result: Mapped[dict[str, Any]] = mapped_column(
        MutableDict.as_mutable(JSONB),
        nullable=False,
        default=dict,
        server_default=text("'{}'::jsonb"),
    )
    error_code: Mapped[str | None] = mapped_column(String(100), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    input_tokens: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default="0",
    )
    output_tokens: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default="0",
    )
    total_tokens: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default="0",
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
