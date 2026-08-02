"""邮件线程与邮件消息 ORM 模型。"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from uuid import UUID

from sqlalchemy import (
    DateTime,
    Enum,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.mutable import MutableDict, MutableList
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.models.mixins import TimestampMixin, UUIDPrimaryKeyMixin


class EmailThreadStatus(StrEnum):
    """邮件线程处理状态。"""

    PENDING = "pending"
    PROCESSING = "processing"
    PROCESSED = "processed"
    FAILED = "failed"


class EmailDirection(StrEnum):
    """邮件相对当前用户的方向。"""

    INBOUND = "inbound"
    OUTBOUND = "outbound"
    DRAFT = "draft"


class EmailThread(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """由同一 Provider 会话标识关联的一组邮件。"""

    __tablename__ = "email_threads"
    __table_args__ = (
        UniqueConstraint(
            "user_id",
            "provider",
            "external_id",
            name="uq_email_threads_user_provider_external",
        ),
        UniqueConstraint("id", "user_id", name="uq_email_threads_id_user"),
        Index("ix_email_threads_user_updated_at", "user_id", "updated_at"),
        Index("ix_email_threads_user_status", "user_id", "status"),
    )

    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    external_id: Mapped[str] = mapped_column(String(255), nullable=False)
    subject: Mapped[str] = mapped_column(String(500), nullable=False)
    participants: Mapped[list[str]] = mapped_column(
        MutableList.as_mutable(JSONB),
        nullable=False,
        default=list,
        server_default=text("'[]'::jsonb"),
    )
    status: Mapped[EmailThreadStatus] = mapped_column(
        Enum(
            EmailThreadStatus,
            name="email_thread_status",
            native_enum=False,
            create_constraint=True,
            validate_strings=True,
            values_callable=lambda enum_type: [item.value for item in enum_type],
        ),
        nullable=False,
        default=EmailThreadStatus.PENDING,
        server_default=EmailThreadStatus.PENDING.value,
    )
    last_message_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    messages: Mapped[list[EmailMessage]] = relationship(
        back_populates="thread",
        cascade="all, delete-orphan",
        passive_deletes=True,
        lazy="raise",
        order_by="EmailMessage.sent_at",
    )


class EmailMessage(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """邮件线程中的一封具体邮件。"""

    __tablename__ = "email_messages"
    __table_args__ = (
        ForeignKeyConstraint(
            ["thread_id", "user_id"],
            ["email_threads.id", "email_threads.user_id"],
            ondelete="CASCADE",
            name="fk_email_messages_thread_user_email_threads",
        ),
        UniqueConstraint(
            "user_id",
            "provider",
            "external_id",
            name="uq_email_messages_user_provider_external",
        ),
        UniqueConstraint(
            "user_id",
            "idempotency_key",
            name="uq_email_messages_user_idempotency_key",
        ),
        Index("ix_email_messages_thread_sent_at", "thread_id", "sent_at"),
        Index("ix_email_messages_user_sent_at", "user_id", "sent_at"),
    )

    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    thread_id: Mapped[UUID] = mapped_column(nullable=False)
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    external_id: Mapped[str] = mapped_column(String(255), nullable=False)
    subject: Mapped[str] = mapped_column(String(500), nullable=False)
    sender: Mapped[str] = mapped_column(String(320), nullable=False)
    recipients: Mapped[list[str]] = mapped_column(
        MutableList.as_mutable(JSONB),
        nullable=False,
        default=list,
        server_default=text("'[]'::jsonb"),
    )
    cc: Mapped[list[str]] = mapped_column(
        MutableList.as_mutable(JSONB),
        nullable=False,
        default=list,
        server_default=text("'[]'::jsonb"),
    )
    body_text: Mapped[str] = mapped_column(Text, nullable=False)
    direction: Mapped[EmailDirection] = mapped_column(
        Enum(
            EmailDirection,
            name="email_direction",
            native_enum=False,
            create_constraint=True,
            validate_strings=True,
            values_callable=lambda enum_type: [item.value for item in enum_type],
        ),
        nullable=False,
    )
    headers: Mapped[dict[str, str]] = mapped_column(
        MutableDict.as_mutable(JSONB),
        nullable=False,
        default=dict,
        server_default=text("'{}'::jsonb"),
    )
    sent_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    idempotency_key: Mapped[str | None] = mapped_column(String(255), nullable=True)

    thread: Mapped[EmailThread] = relationship(back_populates="messages", lazy="raise")
