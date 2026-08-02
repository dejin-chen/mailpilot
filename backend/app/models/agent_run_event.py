"""Agent 执行时间线 ORM 模型。"""

from enum import StrEnum
from typing import Any
from uuid import UUID

from sqlalchemy import (
    Enum,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.mutable import MutableDict
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.mixins import TimestampMixin, UUIDPrimaryKeyMixin


class AgentRunEventType(StrEnum):
    """前端可以稳定识别的 Agent 执行事件类型。"""

    RUN_CREATED = "run_created"
    RUN_STARTED = "run_started"
    RUN_RESUMED = "run_resumed"
    NODE_COMPLETED = "node_completed"
    APPROVAL_REQUIRED = "approval_required"
    RUN_COMPLETED = "run_completed"
    RUN_FAILED = "run_failed"
    RUN_CANCELLED = "run_cancelled"


class AgentRunEvent(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """一条可回放的 Agent 执行事件，不保存原始邮件和完整 Prompt。"""

    __tablename__ = "agent_run_events"
    __table_args__ = (
        ForeignKeyConstraint(
            ["agent_run_id", "user_id"],
            ["agent_runs.id", "agent_runs.user_id"],
            ondelete="CASCADE",
            name="fk_agent_run_events_agent_run_user_agent_runs",
        ),
        UniqueConstraint(
            "agent_run_id",
            "sequence",
            name="uq_agent_run_events_run_sequence",
        ),
        Index(
            "ix_agent_run_events_user_run_sequence",
            "user_id",
            "agent_run_id",
            "sequence",
        ),
    )

    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    agent_run_id: Mapped[UUID] = mapped_column(nullable=False)
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    event_type: Mapped[AgentRunEventType] = mapped_column(
        Enum(
            AgentRunEventType,
            name="agent_run_event_type",
            native_enum=False,
            create_constraint=True,
            validate_strings=True,
            values_callable=lambda enum_type: [item.value for item in enum_type],
        ),
        nullable=False,
    )
    node_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    payload: Mapped[dict[str, Any]] = mapped_column(
        MutableDict.as_mutable(JSONB),
        nullable=False,
        default=dict,
    )
