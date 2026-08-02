"""日历事件导入、查询与可用性 Schema。"""

from datetime import datetime
from typing import Self
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator, model_validator

from app.models.calendar import CalendarEventStatus
from app.schemas.common import ProviderName


class CalendarEventImport(BaseModel):
    """向本地测试日历导入一条已存在事件。"""

    provider: ProviderName = "local"
    external_id: str = Field(min_length=1, max_length=255)
    title: str = Field(min_length=1, max_length=500)
    description: str = ""
    start_at: datetime
    end_at: datetime
    timezone: str = Field(default="UTC", min_length=1, max_length=64)
    attendees: list[EmailStr] = Field(default_factory=list)
    location: str | None = Field(default=None, max_length=500)
    is_all_day: bool = False
    status: CalendarEventStatus = CalendarEventStatus.CONFIRMED
    idempotency_key: str | None = Field(default=None, max_length=255)

    @field_validator("provider", mode="before")
    @classmethod
    def normalize_provider(cls, value: object) -> object:
        return value.strip().lower() if isinstance(value, str) else value

    @field_validator("external_id")
    @classmethod
    def normalize_external_id(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            msg = "外部 ID 不能为空"
            raise ValueError(msg)
        return normalized

    @field_validator("title")
    @classmethod
    def normalize_title(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            msg = "日程标题不能为空"
            raise ValueError(msg)
        return normalized

    @field_validator("attendees", mode="before")
    @classmethod
    def normalize_attendees(cls, value: object) -> object:
        if not isinstance(value, list):
            return value
        return [item.strip().lower() if isinstance(item, str) else item for item in value]

    @field_validator("timezone")
    @classmethod
    def validate_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except ZoneInfoNotFoundError as exc:
            msg = f"无效时区：{value}"
            raise ValueError(msg) from exc
        return value

    @field_validator("location")
    @classmethod
    def normalize_location(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None

    @field_validator("idempotency_key")
    @classmethod
    def normalize_idempotency_key(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None

    @model_validator(mode="after")
    def validate_time_range(self) -> Self:
        if self.start_at.utcoffset() is None or self.end_at.utcoffset() is None:
            msg = "日程开始和结束时间必须包含时区"
            raise ValueError(msg)
        if self.start_at >= self.end_at:
            msg = "日程结束时间必须晚于开始时间"
            raise ValueError(msg)
        return self


class CalendarEventResponse(BaseModel):
    """日历事件公开响应。"""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    provider: str
    external_id: str
    title: str
    description: str
    start_at: datetime
    end_at: datetime
    timezone: str
    attendees: list[EmailStr]
    location: str | None
    is_all_day: bool
    status: CalendarEventStatus
    idempotency_key: str | None
    created_at: datetime
    updated_at: datetime


class CalendarAvailabilityResponse(BaseModel):
    """给定时间范围的冲突查询结果。"""

    available: bool
    conflicts: list[CalendarEventResponse]
