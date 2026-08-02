"""审批创建、决定和响应 Schema。"""

from datetime import datetime
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

from app.models.approval import ApprovalAction, ApprovalStatus


class ApprovalActionArguments(BaseModel):
    """所有危险写操作参数共同使用的严格配置。"""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class SendEmailArguments(ApprovalActionArguments):
    """发送邮件审批需要的业务参数。"""

    draft_message_id: UUID


class CreateEventArguments(ApprovalActionArguments):
    """创建日历事件审批需要的业务参数。"""

    title: str = Field(min_length=1, max_length=500)
    start_at: datetime
    end_at: datetime
    attendees: list[EmailStr] = Field(default_factory=list, max_length=100)
    timezone: str = Field(min_length=1, max_length=64)

    @model_validator(mode="after")
    def validate_time_range(self) -> Self:
        """会议时间必须带时区，并且结束时间晚于开始时间。"""

        if self.start_at.utcoffset() is None or self.end_at.utcoffset() is None:
            msg = "创建会议的开始和结束时间必须包含时区"
            raise ValueError(msg)
        if self.start_at >= self.end_at:
            msg = "创建会议的结束时间必须晚于开始时间"
            raise ValueError(msg)
        try:
            ZoneInfo(self.timezone)
        except ZoneInfoNotFoundError as exc:
            msg = f"无效会议时区：{self.timezone}"
            raise ValueError(msg) from exc
        return self


class RescheduleEventArguments(ApprovalActionArguments):
    """修改会议时间审批需要的业务参数。"""

    event_id: UUID
    new_start_at: datetime
    new_end_at: datetime

    @model_validator(mode="after")
    def validate_time_range(self) -> Self:
        """新时间必须带时区，并且结束时间晚于开始时间。"""

        if self.new_start_at.utcoffset() is None or self.new_end_at.utcoffset() is None:
            msg = "修改会议的开始和结束时间必须包含时区"
            raise ValueError(msg)
        if self.new_start_at >= self.new_end_at:
            msg = "修改会议的结束时间必须晚于开始时间"
            raise ValueError(msg)
        return self


class CancelEventArguments(ApprovalActionArguments):
    """取消会议审批需要的业务参数。"""

    event_id: UUID


type WriteActionArguments = (
    SendEmailArguments | CreateEventArguments | RescheduleEventArguments | CancelEventArguments
)

WRITE_ACTION_ARGUMENT_MODELS: dict[ApprovalAction, type[ApprovalActionArguments]] = {
    ApprovalAction.SEND_EMAIL: SendEmailArguments,
    ApprovalAction.CREATE_EVENT: CreateEventArguments,
    ApprovalAction.RESCHEDULE_EVENT: RescheduleEventArguments,
    ApprovalAction.CANCEL_EVENT: CancelEventArguments,
}


class ApprovalRequestCreate(BaseModel):
    """Graph 准备危险操作时创建审批单。"""

    agent_run_id: UUID
    action: ApprovalAction
    proposed_arguments: dict[str, JsonValue]
    idempotency_key: str = Field(min_length=1, max_length=255)
    version: int = Field(default=1, ge=1)

    @field_validator("idempotency_key")
    @classmethod
    def normalize_idempotency_key(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            msg = "审批幂等键不能为空"
            raise ValueError(msg)
        return normalized


class ApprovalDecision(BaseModel):
    """用户对待审批操作作出的结构化决定。"""

    status: ApprovalStatus
    modified_arguments: dict[str, JsonValue] | None = None
    feedback: str | None = Field(default=None, max_length=2000)

    @field_validator("feedback")
    @classmethod
    def normalize_feedback(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None

    @model_validator(mode="after")
    def validate_decision(self) -> "ApprovalDecision":
        allowed = {
            ApprovalStatus.APPROVED,
            ApprovalStatus.REJECTED,
            ApprovalStatus.FEEDBACK_REQUESTED,
        }
        if self.status not in allowed:
            msg = "用户只能接受、拒绝或要求根据反馈重新生成"
            raise ValueError(msg)
        if self.status is ApprovalStatus.FEEDBACK_REQUESTED and not self.feedback:
            msg = "要求重新生成时必须提供反馈"
            raise ValueError(msg)
        if self.status is not ApprovalStatus.APPROVED and self.modified_arguments is not None:
            msg = "只有修改后接受才能携带 modified_arguments"
            raise ValueError(msg)
        return self


class ApprovalRejectRequest(BaseModel):
    """拒绝审批时允许附带的原因。"""

    feedback: str | None = Field(default=None, max_length=2000)

    @field_validator("feedback")
    @classmethod
    def normalize_feedback(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None


class ApprovalModifyRequest(BaseModel):
    """修改危险操作参数后接受审批。"""

    modified_arguments: dict[str, JsonValue] = Field(min_length=1)


class ApprovalFeedbackRequest(BaseModel):
    """要求 Agent 根据明确文字反馈重新生成方案。"""

    feedback: str = Field(min_length=1, max_length=2000)

    @field_validator("feedback")
    @classmethod
    def normalize_feedback(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            msg = "重新生成反馈不能为空"
            raise ValueError(msg)
        return normalized


class ApprovalRequestResponse(BaseModel):
    """审批中心展示的一张完整审批单。"""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    user_id: UUID
    agent_run_id: UUID
    action: ApprovalAction
    status: ApprovalStatus
    proposed_arguments: dict[str, JsonValue]
    modified_arguments: dict[str, JsonValue] | None
    feedback: str | None
    idempotency_key: str
    version: int
    decided_by_user_id: UUID | None
    decided_at: datetime | None
    executed_at: datetime | None
    execution_result: dict[str, JsonValue] | None
    created_at: datetime
    updated_at: datetime


class ApprovalDecisionResponse(BaseModel):
    """审批决定落库并尝试恢复 Graph 后的结果。"""

    approval: ApprovalRequestResponse
    graph_resumed: bool
    already_resumed: bool
    next_approval_request_id: UUID | None = None
