"""人工审批闸门使用的结构化写操作方案与暂停载荷。"""

from enum import StrEnum
from typing import Self
from uuid import UUID

from pydantic import Field, JsonValue, model_validator

from app.agent.schemas import AgentSchema
from app.models.approval import ApprovalAction, ApprovalStatus
from app.schemas.approval import (
    WRITE_ACTION_ARGUMENT_MODELS,
    CancelEventArguments,
    CreateEventArguments,
    RescheduleEventArguments,
    SendEmailArguments,
    WriteActionArguments,
)

__all__ = [
    "ApprovalGateStatus",
    "ApprovalInterruptPayload",
    "ApprovalResumeSignal",
    "CancelEventArguments",
    "CreateEventArguments",
    "RescheduleEventArguments",
    "SendEmailArguments",
    "WriteActionArguments",
    "WriteActionExecution",
    "WriteActionProposal",
    "WriteExecutionStatus",
]


class WriteActionProposal(AgentSchema):
    """尚未执行、必须先让用户确认的一项写操作方案。"""

    action: ApprovalAction
    arguments: WriteActionArguments
    summary: str = Field(min_length=1, max_length=1000)
    version: int = Field(default=1, ge=1)

    @model_validator(mode="after")
    def validate_argument_type(self) -> Self:
        """操作名和参数模型必须严格一一对应，避免把参数发给错误工具。"""

        expected_model = WRITE_ACTION_ARGUMENT_MODELS[self.action]
        if not isinstance(self.arguments, expected_model):
            msg = f"{self.action.value} 的参数结构不正确"
            raise ValueError(msg)
        return self


class ApprovalGateStatus(StrEnum):
    """审批闸门本身的执行状态，不代替数据库审批状态。"""

    PENDING = "pending"
    WAITING_APPROVAL = "waiting_approval"
    RESUMED = "resumed"
    FAILED = "failed"


class WriteExecutionStatus(StrEnum):
    """审批通过后写工具节点的结果状态。"""

    SUCCEEDED = "succeeded"
    FAILED = "failed"
    UNCERTAIN = "uncertain"


class WriteActionExecution(AgentSchema):
    """写工具执行后存入 Graph State 的安全结果。"""

    action: ApprovalAction
    tool_name: str
    idempotency_key: str
    arguments: dict[str, JsonValue]
    result: dict[str, JsonValue]


class ApprovalResumeSignal(AgentSchema):
    """审批 API 恢复 Graph 时传回的最小信号。"""

    approval_request_id: UUID
    status: ApprovalStatus
    feedback: str | None = Field(default=None, max_length=2000)

    @model_validator(mode="after")
    def validate_terminal_user_decision(self) -> Self:
        """只接收用户可以作出的三种决定，不接收内部执行状态。"""

        allowed = {
            ApprovalStatus.APPROVED,
            ApprovalStatus.REJECTED,
            ApprovalStatus.FEEDBACK_REQUESTED,
        }
        if self.status not in allowed:
            msg = "恢复信号必须是接受、拒绝或要求重新生成"
            raise ValueError(msg)
        if self.status is ApprovalStatus.FEEDBACK_REQUESTED and not self.feedback:
            msg = "要求重新生成时恢复信号必须携带反馈"
            raise ValueError(msg)
        if self.status is not ApprovalStatus.FEEDBACK_REQUESTED and self.feedback is not None:
            msg = "只有要求重新生成时才能携带反馈"
            raise ValueError(msg)
        return self


class ApprovalInterruptPayload(AgentSchema):
    """通过 interrupt 暴露给审批页面的 JSON 安全载荷。"""

    approval_request_id: UUID
    agent_run_id: UUID
    action: ApprovalAction
    proposed_arguments: dict[str, object]
    summary: str
    version: int
