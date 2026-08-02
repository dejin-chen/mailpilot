"""MailPilot LangGraph 的共享 Typed State。"""

import operator
from datetime import datetime
from typing import Annotated, Required, TypedDict
from uuid import UUID

from app.agent.approval_schemas import (
    ApprovalGateStatus,
    ApprovalResumeSignal,
    WriteActionExecution,
    WriteActionProposal,
    WriteExecutionStatus,
)
from app.agent.schemas import (
    AgentError,
    AgentRunStatus,
    EmailClassification,
    EmailDraft,
    ExecutionPlan,
    ExtractedIntent,
    FinalResult,
    ModelUsage,
    ToolExecutionResult,
)
from app.memory.schemas import AgentMemoryContext
from app.models.approval import ApprovalStatus
from app.schemas.memory_feedback import (
    FeedbackMemoryDecision,
    MemoryFeedbackUpdateResult,
)


class MailAgentState(TypedDict, total=False):
    """节点共用的原始数据和结构化结果，不保存 Prompt 模板。"""

    email_thread_id: Required[UUID]
    email_message_id: UUID
    email_subject: str
    email_body: str
    sender: str
    recipients: list[str]
    cc: list[str]
    email_sent_at: datetime

    classification: EmailClassification
    intent: ExtractedIntent
    plan: ExecutionPlan
    memory_context: AgentMemoryContext

    tool_results: Annotated[list[ToolExecutionResult], operator.add]
    model_usages: Annotated[list[ModelUsage], operator.add]
    errors: Annotated[list[AgentError], operator.add]
    tool_call_count: int
    max_tool_calls: int

    draft: EmailDraft
    draft_message_id: UUID
    proposal: WriteActionProposal
    proposal_version: int
    approval_request_id: UUID
    approval_status: ApprovalStatus
    gate_status: ApprovalGateStatus
    resume_signal: ApprovalResumeSignal
    execution_status: WriteExecutionStatus
    execution: WriteActionExecution
    regeneration_feedback: str
    memory_feedback_decision: FeedbackMemoryDecision
    memory_update: MemoryFeedbackUpdateResult
    regeneration_count: int
    max_regenerations: int
    error_code: str
    error_message: str
    current_node: str
    run_status: AgentRunStatus
    final_result: FinalResult


class MailAgentInput(TypedDict):
    """启动工作流时允许上层传入的最小数据。"""

    email_thread_id: UUID


class MailAgentOutput(TypedDict, total=False):
    """工作流结束后对上层有意义的结果。"""

    run_status: AgentRunStatus
    current_node: str
    classification: EmailClassification
    intent: ExtractedIntent
    plan: ExecutionPlan
    memory_context: AgentMemoryContext
    tool_results: list[ToolExecutionResult]
    tool_call_count: int
    model_usages: list[ModelUsage]
    draft: EmailDraft
    draft_message_id: UUID
    proposal: WriteActionProposal
    approval_request_id: UUID
    approval_status: ApprovalStatus
    gate_status: ApprovalGateStatus
    memory_feedback_decision: FeedbackMemoryDecision
    memory_update: MemoryFeedbackUpdateResult
    execution_status: WriteExecutionStatus
    execution: WriteActionExecution
    errors: list[AgentError]
    final_result: FinalResult


def create_initial_state(
    *,
    email_thread_id: UUID,
    max_tool_calls: int = 4,
    max_regenerations: int = 2,
) -> MailAgentState:
    """建立计数器和追加型字段，避免各入口重复拼初始 State。"""

    if not 1 <= max_tool_calls <= 10:
        msg = "max_tool_calls 必须在 1 到 10 之间"
        raise ValueError(msg)
    if not 0 <= max_regenerations <= 5:
        msg = "max_regenerations 必须在 0 到 5 之间"
        raise ValueError(msg)
    return MailAgentState(
        email_thread_id=email_thread_id,
        tool_results=[],
        model_usages=[],
        errors=[],
        tool_call_count=0,
        max_tool_calls=max_tool_calls,
        proposal_version=1,
        regeneration_count=0,
        max_regenerations=max_regenerations,
        current_node="start",
        run_status=AgentRunStatus.PENDING,
    )
