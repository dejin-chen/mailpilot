"""企业日历事件 ORM 模型。"""

from datetime import datetime
from enum import StrEnum
from uuid import UUID

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
    false,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.mutable import MutableList
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.mixins import TimestampMixin, UUIDPrimaryKeyMixin


class CalendarEventStatus(StrEnum):
    """本地日历事件状态。"""

    TENTATIVE = "tentative"
    CONFIRMED = "confirmed"
    CANCELLED = "cancelled"


class CalendarEvent(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """属于单个用户的一条日历事件。"""

    __tablename__ = "calendar_events"
    __table_args__ = (
        CheckConstraint("start_at < end_at", name="valid_time_range"),
        UniqueConstraint(
            "user_id",
            "provider",
            "external_id",
            name="uq_calendar_events_user_provider_external",
        ),
        UniqueConstraint(
            "user_id",
            "idempotency_key",
            name="uq_calendar_events_user_idempotency_key",
        ),
        Index("ix_calendar_events_user_time", "user_id", "start_at", "end_at"),
        Index("ix_calendar_events_user_status", "user_id", "status"),
    )

    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    external_id: Mapped[str] = mapped_column(String(255), nullable=False)
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    description: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        default="",
        server_default="",
    )
    start_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    end_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    timezone: Mapped[str] = mapped_column(String(64), nullable=False)
    attendees: Mapped[list[str]] = mapped_column(
        MutableList.as_mutable(JSONB),
        nullable=False,
        default=list,
        server_default=text("'[]'::jsonb"),
    )
    location: Mapped[str | None] = mapped_column(String(500), nullable=True)
    is_all_day: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default=false(),
    )
    status: Mapped[CalendarEventStatus] = mapped_column(
        Enum(
            CalendarEventStatus,
            name="calendar_event_status",
            native_enum=False,
            create_constraint=True,
            validate_strings=True,
            values_callable=lambda enum_type: [item.value for item in enum_type],
        ),
        nullable=False,
        default=CalendarEventStatus.CONFIRMED,
        server_default=CalendarEventStatus.CONFIRMED.value,
    )
    idempotency_key: Mapped[str | None] = mapped_column(String(255), nullable=True)
