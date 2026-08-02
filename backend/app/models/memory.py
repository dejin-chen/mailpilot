"""用户长期记忆档案及不可变版本 ORM 模型。"""

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
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.mutable import MutableDict
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.mixins import TimestampMixin, UUIDPrimaryKeyMixin


class MemoryType(StrEnum):
    """第一版允许持久化的长期记忆类别。"""

    EMAIL_STYLE = "email_style"
    CALENDAR_PREFERENCES = "calendar_preferences"
    CONTACT = "contact"


class MemorySourceType(StrEnum):
    """记忆版本的可信来源。"""

    USER_API_EDIT = "user_api_edit"
    APPROVAL_FEEDBACK = "approval_feedback"
    DRAFT_MODIFICATION = "draft_modification"
    CALENDAR_MODIFICATION = "calendar_modification"


class MemoryProfile(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """一项具有稳定 ID 和当前版本号的用户长期记忆。"""

    __tablename__ = "memory_profiles"
    __table_args__ = (
        CheckConstraint("current_version >= 1", name="positive_current_version"),
        UniqueConstraint("id", "user_id", name="uq_memory_profiles_id_user"),
        UniqueConstraint(
            "user_id",
            "memory_type",
            "memory_key",
            name="uq_memory_profiles_user_type_key",
        ),
        Index(
            "ix_memory_profiles_user_type_updated",
            "user_id",
            "memory_type",
            "updated_at",
        ),
    )

    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    memory_type: Mapped[MemoryType] = mapped_column(
        Enum(
            MemoryType,
            name="memory_type",
            native_enum=False,
            create_constraint=True,
            validate_strings=True,
            values_callable=lambda enum_type: [item.value for item in enum_type],
        ),
        nullable=False,
    )
    memory_key: Mapped[str] = mapped_column(String(255), nullable=False)
    current_version: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=1,
        server_default="1",
    )


class MemoryProfileVersion(UUIDPrimaryKeyMixin, Base):
    """长期记忆的一次不可变内容版本。"""

    __tablename__ = "memory_profile_versions"
    __table_args__ = (
        CheckConstraint("version >= 1", name="positive_version"),
        ForeignKeyConstraint(
            ["memory_profile_id", "user_id"],
            ["memory_profiles.id", "memory_profiles.user_id"],
            ondelete="CASCADE",
            name="fk_memory_profile_versions_profile_user_memory_profiles",
        ),
        UniqueConstraint(
            "memory_profile_id",
            "version",
            name="uq_memory_profile_versions_profile_version",
        ),
        UniqueConstraint(
            "user_id",
            "source_type",
            "source_reference_type",
            "source_reference_id",
            name="uq_memory_profile_versions_user_source_reference",
        ),
        Index(
            "ix_memory_profile_versions_user_profile_version",
            "user_id",
            "memory_profile_id",
            "version",
        ),
    )

    memory_profile_id: Mapped[UUID] = mapped_column(nullable=False)
    user_id: Mapped[UUID] = mapped_column(nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    value: Mapped[dict[str, Any]] = mapped_column(
        MutableDict.as_mutable(JSONB),
        nullable=False,
        default=dict,
        server_default=text("'{}'::jsonb"),
    )
    source_type: Mapped[MemorySourceType] = mapped_column(
        Enum(
            MemorySourceType,
            name="memory_source_type",
            native_enum=False,
            create_constraint=True,
            validate_strings=True,
            values_callable=lambda enum_type: [item.value for item in enum_type],
        ),
        nullable=False,
    )
    source_reference_type: Mapped[str | None] = mapped_column(
        String(100),
        nullable=True,
    )
    source_reference_id: Mapped[UUID | None] = mapped_column(nullable=True)
    created_by_user_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
    )
