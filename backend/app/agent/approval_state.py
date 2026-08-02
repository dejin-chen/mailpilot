"""人工审批闸门的 Typed State。"""

from typing import Required, TypedDict
from uuid import UUID

from app.agent.approval_schemas import (
    ApprovalGateStatus,
    ApprovalResumeSignal,
    WriteActionExecution,
    WriteActionProposal,
    WriteExecutionStatus,
)
from app.models.approval import ApprovalStatus


class ApprovalGateState(TypedDict, total=False):
    """写操作方案从创建审批单到暂停、恢复所需的最小状态。"""

    proposal: Required[WriteActionProposal]
    approval_request_id: UUID
    approval_status: ApprovalStatus
    gate_status: ApprovalGateStatus
    current_node: str
    resume_signal: ApprovalResumeSignal
    regeneration_feedback: str
    execution_status: WriteExecutionStatus
    execution: WriteActionExecution
    error_code: str
    error_message: str


class ApprovalGateInput(TypedDict):
    """进入审批闸门时只允许传入结构化写操作方案。"""

    proposal: WriteActionProposal


class ApprovalGateOutput(TypedDict, total=False):
    """审批闸门恢复后允许上层读取的结果。"""

    approval_request_id: UUID
    approval_status: ApprovalStatus
    gate_status: ApprovalGateStatus
    current_node: str
    resume_signal: ApprovalResumeSignal
    execution_status: WriteExecutionStatus
    execution: WriteActionExecution
    error_code: str
    error_message: str
