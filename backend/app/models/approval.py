"""人工审批请求 ORM 模型。"""

from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
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


class ApprovalAction(StrEnum):
    """必须经过人工审批的外部副作用操作。"""

    SEND_EMAIL = "send_email"
    CREATE_EVENT = "create_event"
    RESCHEDULE_EVENT = "reschedule_event"
    CANCEL_EVENT = "cancel_event"


class ApprovalStatus(StrEnum):
    """审批从待决定到执行结束的状态。"""

    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    FEEDBACK_REQUESTED = "feedback_requested"
    EXECUTING = "executing"
    EXECUTED = "executed"
    EXECUTION_FAILED = "execution_failed"


class ApprovalRequest(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """用户能够查看、修改和决定的一次危险操作审批单。"""

    __tablename__ = "approval_requests"
    __table_args__ = (
        ForeignKeyConstraint(
            ["agent_run_id", "user_id"],
            ["agent_runs.id", "agent_runs.user_id"],
            ondelete="CASCADE",
            name="fk_approval_requests_agent_run_user_agent_runs",
        ),
        CheckConstraint("version >= 1", name="positive_version"),
        UniqueConstraint(
            "user_id",
            "idempotency_key",
            name="uq_approval_requests_user_idempotency_key",
        ),
        UniqueConstraint(
            "agent_run_id",
            "action",
            "version",
            name="uq_approval_requests_run_action_version",
        ),
        UniqueConstraint("id", "user_id", name="uq_approval_requests_id_user"),
        Index(
            "ix_approval_requests_user_status_created",
            "user_id",
            "status",
            "created_at",
        ),
        Index("ix_approval_requests_agent_run", "agent_run_id", "created_at"),
    )

    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    agent_run_id: Mapped[UUID] = mapped_column(nullable=False)
    action: Mapped[ApprovalAction] = mapped_column(
        Enum(
            ApprovalAction,
            name="approval_action",
            native_enum=False,
            create_constraint=True,
            validate_strings=True,
            values_callable=lambda enum_type: [item.value for item in enum_type],
        ),
        nullable=False,
    )
    status: Mapped[ApprovalStatus] = mapped_column(
        Enum(
            ApprovalStatus,
            name="approval_status",
            native_enum=False,
            create_constraint=True,
            validate_strings=True,
            values_callable=lambda enum_type: [item.value for item in enum_type],
        ),
        nullable=False,
        default=ApprovalStatus.PENDING,
        server_default=ApprovalStatus.PENDING.value,
    )
    proposed_arguments: Mapped[dict[str, Any]] = mapped_column(
        MutableDict.as_mutable(JSONB),
        nullable=False,
    )
    modified_arguments: Mapped[dict[str, Any] | None] = mapped_column(
        MutableDict.as_mutable(JSONB),
        nullable=True,
    )
    feedback: Mapped[str | None] = mapped_column(Text, nullable=True)
    idempotency_key: Mapped[str] = mapped_column(String(255), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
    decided_by_user_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    executed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    execution_result: Mapped[dict[str, Any] | None] = mapped_column(
        MutableDict.as_mutable(JSONB),
        nullable=True,
    )
