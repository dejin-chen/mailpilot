"""MCP 工具调用执行记录 ORM 模型。"""

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
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.mutable import MutableDict
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.mixins import TimestampMixin, UUIDPrimaryKeyMixin


class ToolCallStatus(StrEnum):
    """一次有副作用工具调用的持久化状态。"""

    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    UNCERTAIN = "uncertain"


class ToolCallLog(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """记录写工具参数、幂等键、结果与错误，供恢复和审计使用。"""

    __tablename__ = "tool_call_logs"
    __table_args__ = (
        ForeignKeyConstraint(
            ["agent_run_id", "user_id"],
            ["agent_runs.id", "agent_runs.user_id"],
            ondelete="CASCADE",
            name="fk_tool_call_logs_agent_run_user_agent_runs",
        ),
        ForeignKeyConstraint(
            ["approval_request_id", "user_id"],
            ["approval_requests.id", "approval_requests.user_id"],
            ondelete="CASCADE",
            name="fk_tool_call_logs_approval_user_approval_requests",
        ),
        UniqueConstraint(
            "user_id",
            "idempotency_key",
            name="uq_tool_call_logs_user_idempotency_key",
        ),
        Index("ix_tool_call_logs_agent_run_created", "agent_run_id", "created_at"),
        Index("ix_tool_call_logs_approval_created", "approval_request_id", "created_at"),
        Index("ix_tool_call_logs_user_status_created", "user_id", "status", "created_at"),
    )

    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    agent_run_id: Mapped[UUID] = mapped_column(nullable=False)
    approval_request_id: Mapped[UUID] = mapped_column(nullable=False)
    tool_name: Mapped[str] = mapped_column(String(100), nullable=False)
    status: Mapped[ToolCallStatus] = mapped_column(
        Enum(
            ToolCallStatus,
            name="tool_call_status",
            native_enum=False,
            create_constraint=True,
            validate_strings=True,
            values_callable=lambda enum_type: [item.value for item in enum_type],
        ),
        nullable=False,
        default=ToolCallStatus.RUNNING,
        server_default=ToolCallStatus.RUNNING.value,
    )
    arguments: Mapped[dict[str, Any]] = mapped_column(
        MutableDict.as_mutable(JSONB),
        nullable=False,
    )
    result: Mapped[dict[str, Any] | None] = mapped_column(
        MutableDict.as_mutable(JSONB),
        nullable=True,
    )
    error_code: Mapped[str | None] = mapped_column(String(100), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    idempotency_key: Mapped[str] = mapped_column(String(255), nullable=False)
    request_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    attempt_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=1,
        server_default="1",
    )
    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
