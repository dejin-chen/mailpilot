"""审批反馈触发长期记忆更新时使用的受限结构化 Schema。"""

from typing import Annotated, Literal, Self
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import (
    BaseModel,
    ConfigDict,
    EmailStr,
    Field,
    field_validator,
    model_validator,
)

from app.models.memory import MemoryType


class MemoryPatch(BaseModel):
    """记忆局部修改的严格基类；模型不能添加未声明字段。"""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class EmailStyleMemoryPatch(MemoryPatch):
    """允许从明确反馈中提取的邮件风格局部修改。"""

    tone: str | None = Field(default=None, min_length=1, max_length=500)
    signature: str | None = Field(default=None, min_length=1, max_length=2000)
    salutation: str | None = Field(default=None, min_length=1, max_length=500)

    @model_validator(mode="after")
    def require_change(self) -> Self:
        if self.tone is None and self.signature is None and self.salutation is None:
            raise ValueError("邮件风格 Patch 至少需要一个修改字段")
        return self


class CalendarPreferencesMemoryPatch(MemoryPatch):
    """第一版只允许从反馈更新低歧义的日历标量偏好。"""

    timezone: str | None = Field(default=None, min_length=1, max_length=64)
    default_duration_minutes: int | None = Field(default=None, ge=5, le=480)

    @field_validator("timezone")
    @classmethod
    def validate_timezone(cls, value: str | None) -> str | None:
        if value is None:
            return None
        try:
            ZoneInfo(value)
        except ZoneInfoNotFoundError as exc:
            raise ValueError(f"无效时区：{value}") from exc
        return value

    @model_validator(mode="after")
    def require_change(self) -> Self:
        if self.timezone is None and self.default_duration_minutes is None:
            raise ValueError("日历偏好 Patch 至少需要一个修改字段")
        return self


class ContactMemoryPatch(MemoryPatch):
    """允许更新当前邮件发件人的稳定称呼和重要性。"""

    display_name: str | None = Field(default=None, min_length=1, max_length=200)
    salutation: str | None = Field(default=None, min_length=1, max_length=500)
    important: bool | None = None

    @model_validator(mode="after")
    def require_change(self) -> Self:
        if self.display_name is None and self.salutation is None and self.important is None:
            raise ValueError("联系人 Patch 至少需要一个修改字段")
        return self


class EmailStyleMemoryUpdateProposal(MemoryPatch):
    """邮件风格记忆更新建议。"""

    memory_type: Literal[MemoryType.EMAIL_STYLE]
    memory_key: Literal["default"] = "default"
    patch: EmailStyleMemoryPatch
    evidence: str = Field(min_length=1, max_length=1000)
    reason: str = Field(min_length=1, max_length=1000)


class CalendarPreferencesMemoryUpdateProposal(MemoryPatch):
    """日历偏好记忆更新建议。"""

    memory_type: Literal[MemoryType.CALENDAR_PREFERENCES]
    memory_key: Literal["default"] = "default"
    patch: CalendarPreferencesMemoryPatch
    evidence: str = Field(min_length=1, max_length=1000)
    reason: str = Field(min_length=1, max_length=1000)


class ContactMemoryUpdateProposal(MemoryPatch):
    """当前邮件发件人的联系人记忆更新建议。"""

    memory_type: Literal[MemoryType.CONTACT]
    memory_key: EmailStr
    patch: ContactMemoryPatch
    evidence: str = Field(min_length=1, max_length=1000)
    reason: str = Field(min_length=1, max_length=1000)

    @field_validator("memory_key")
    @classmethod
    def normalize_email(cls, value: EmailStr) -> str:
        return str(value).lower()


MemoryUpdateProposal = Annotated[
    EmailStyleMemoryUpdateProposal
    | CalendarPreferencesMemoryUpdateProposal
    | ContactMemoryUpdateProposal,
    Field(discriminator="memory_type"),
]


class FeedbackMemoryDecision(MemoryPatch):
    """LLM 对一条明确长期反馈给出的结构化判断。"""

    should_update: bool
    proposal: MemoryUpdateProposal | None = None
    reason: str = Field(min_length=1, max_length=1000)
    confidence: float = Field(ge=0, le=1)

    @model_validator(mode="after")
    def validate_proposal_presence(self) -> Self:
        if self.should_update and self.proposal is None:
            raise ValueError("需要更新记忆时必须提供 proposal")
        if not self.should_update and self.proposal is not None:
            raise ValueError("不更新记忆时不能提供 proposal")
        return self


class MemoryFeedbackUpdateResult(MemoryPatch):
    """记忆 Service 写回 Graph State 的最小安全结果。"""

    applied: bool
    reused: bool
    memory_id: UUID
    memory_type: MemoryType
    memory_key: str = Field(min_length=1, max_length=255)
    version: int = Field(ge=1)

    @model_validator(mode="after")
    def validate_status(self) -> Self:
        if self.applied == self.reused:
            raise ValueError("applied 和 reused 必须且只能有一个为 true")
        return self
