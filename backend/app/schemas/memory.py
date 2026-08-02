"""长期记忆值、版本化修改和 API 响应 Schema。"""

from datetime import datetime, time
from typing import Self
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import (
    BaseModel,
    ConfigDict,
    EmailStr,
    Field,
    JsonValue,
    field_validator,
    model_validator,
)

from app.models.memory import MemorySourceType, MemoryType


class MemoryValue(BaseModel):
    """所有长期记忆值共同使用的严格结构。"""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class EmailStyleMemory(MemoryValue):
    """用户撰写邮件时的稳定风格偏好。"""

    tone: str | None = Field(default=None, min_length=1, max_length=500)
    signature: str | None = Field(default=None, min_length=1, max_length=2000)
    salutation: str | None = Field(default=None, min_length=1, max_length=500)

    @model_validator(mode="after")
    def require_at_least_one_preference(self) -> Self:
        if self.tone is None and self.signature is None and self.salutation is None:
            msg = "邮件风格记忆至少需要 tone、signature 或 salutation 中的一项"
            raise ValueError(msg)
        return self


class MeetingWindow(MemoryValue):
    """一组每周重复的会议时间窗口。"""

    weekdays: list[int] = Field(min_length=1, max_length=7)
    start_time: time
    end_time: time

    @field_validator("weekdays")
    @classmethod
    def normalize_weekdays(cls, value: list[int]) -> list[int]:
        normalized = sorted(set(value))
        if any(day < 1 or day > 7 for day in normalized):
            msg = "weekday 必须在 1（周一）到 7（周日）之间"
            raise ValueError(msg)
        return normalized

    @model_validator(mode="after")
    def validate_time_range(self) -> Self:
        if self.start_time >= self.end_time:
            msg = "会议偏好窗口的结束时间必须晚于开始时间"
            raise ValueError(msg)
        return self


class CalendarPreferencesMemory(MemoryValue):
    """用户长期日程安排偏好。"""

    timezone: str | None = Field(default=None, min_length=1, max_length=64)
    preferred_windows: list[MeetingWindow] = Field(default_factory=list, max_length=20)
    blocked_windows: list[MeetingWindow] = Field(default_factory=list, max_length=20)
    default_duration_minutes: int | None = Field(default=None, ge=5, le=480)

    @field_validator("timezone")
    @classmethod
    def validate_timezone(cls, value: str | None) -> str | None:
        if value is None:
            return None
        try:
            ZoneInfo(value)
        except ZoneInfoNotFoundError as exc:
            msg = f"无效时区：{value}"
            raise ValueError(msg) from exc
        return value

    @model_validator(mode="after")
    def require_at_least_one_preference(self) -> Self:
        if (
            self.timezone is None
            and not self.preferred_windows
            and not self.blocked_windows
            and self.default_duration_minutes is None
        ):
            msg = "日历偏好记忆至少需要一个有效字段"
            raise ValueError(msg)
        return self


class ContactMemory(MemoryValue):
    """一个重要联系人的稳定称呼和备注。"""

    email: EmailStr
    display_name: str | None = Field(default=None, min_length=1, max_length=200)
    salutation: str | None = Field(default=None, min_length=1, max_length=500)
    important: bool = False


MEMORY_VALUE_MODELS: dict[MemoryType, type[MemoryValue]] = {
    MemoryType.EMAIL_STYLE: EmailStyleMemory,
    MemoryType.CALENDAR_PREFERENCES: CalendarPreferencesMemory,
    MemoryType.CONTACT: ContactMemory,
}


def validate_memory_value(
    memory_type: MemoryType,
    value: dict[str, JsonValue],
) -> MemoryValue:
    """按记忆类型使用唯一对应的严格 Schema 校验值。"""

    return MEMORY_VALUE_MODELS[memory_type].model_validate(value)


class MemoryProfileCreate(BaseModel):
    """用户主动创建一项长期记忆。"""

    model_config = ConfigDict(str_strip_whitespace=True)

    memory_type: MemoryType
    memory_key: str = Field(default="default", min_length=1, max_length=255)
    value: dict[str, JsonValue] = Field(min_length=1)

    @field_validator("memory_key")
    @classmethod
    def normalize_memory_key(cls, value: str) -> str:
        normalized = value.strip().lower()
        if not normalized:
            msg = "记忆 key 不能为空"
            raise ValueError(msg)
        return normalized

    @model_validator(mode="after")
    def validate_typed_value(self) -> Self:
        validate_memory_value(self.memory_type, self.value)
        return self


class MemoryProfileUpdate(BaseModel):
    """以乐观版本号修改一项长期记忆。"""

    memory_type: MemoryType
    expected_version: int = Field(ge=1)
    value: dict[str, JsonValue] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_typed_value(self) -> Self:
        validate_memory_value(self.memory_type, self.value)
        return self


class MemoryProfileResponse(BaseModel):
    """用户可查看的一项当前长期记忆。"""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    memory_type: MemoryType
    memory_key: str
    value: dict[str, JsonValue]
    version: int
    source_type: MemorySourceType
    source_reference_type: str | None
    source_reference_id: UUID | None
    created_at: datetime
    updated_at: datetime


class MemoryProfileVersionResponse(BaseModel):
    """用户可查看的一条不可变长期记忆历史版本。"""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    memory_id: UUID
    version: int
    value: dict[str, JsonValue]
    source_type: MemorySourceType
    source_reference_type: str | None
    source_reference_id: UUID | None
    created_by_user_id: UUID | None
    created_at: datetime


class MemoryDeleteResponse(BaseModel):
    """长期记忆删除结果。"""

    memory_id: UUID
    deleted: bool = True
