"""MCP 工具共享结构化返回 Schema。"""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field

from app.schemas.calendar import CalendarEventResponse
from app.schemas.email import EmailMessageResponse, EmailThreadResponse


class EmailSearchResult(BaseModel):
    """邮件搜索工具的分页结果。"""

    items: list[EmailThreadResponse]
    total: int
    offset: int
    limit: int


class EmailDraftResult(BaseModel):
    """邮件草稿创建或幂等复用结果。"""

    message: EmailMessageResponse
    reused: bool


class MarkEmailProcessedResult(BaseModel):
    """标记邮件线程处理完成的结果。"""

    thread_id: UUID
    status: str


class SendEmailResult(BaseModel):
    """发送邮件工具的结构化结果。"""

    message: EmailMessageResponse
    reused: bool
    approval_id: UUID
    idempotency_key: str


class CalendarAvailabilityResult(BaseModel):
    """日历可用性工具结果。"""

    available: bool
    conflicts: list[CalendarEventResponse]


class AvailableSlot(BaseModel):
    """一个可用于安排会议的候选时间段。"""

    start_at: datetime
    end_at: datetime


class AvailableSlotsResult(BaseModel):
    """可用时段搜索工具结果。"""

    slots: list[AvailableSlot]
    duration_minutes: int = Field(ge=1)


class CalendarWriteResult(BaseModel):
    """创建、改期或取消会议工具的结构化结果。"""

    event: CalendarEventResponse
    reused: bool
    approval_id: UUID
    idempotency_key: str
