"""Agent 输出的确定性安全策略。

LLM 负责理解自然语言，但不能成为权限控制器。本模块只接收已经通过
Pydantic 校验的结构化对象，再用 Python 检查工具范围、资源边界和业务不变量。
"""

from dataclasses import dataclass
from uuid import UUID

from app.agent.schemas import (
    CheckAvailabilityArguments,
    DraftPurpose,
    EmailAction,
    EmailClassification,
    EmailDraft,
    ExecutionPlan,
    ExtractedIntent,
    GetEmailThreadArguments,
    PlanAction,
    ReadOnlyToolName,
)


@dataclass(frozen=True, slots=True)
class AgentPolicyViolation:
    """可以安全写入 Agent State 的策略拒绝结果。"""

    code: str
    message: str


def validate_plan_policy(
    *,
    plan: ExecutionPlan,
    classification: EmailClassification,
    intent: ExtractedIntent,
    current_thread_id: UUID,
    remaining_tool_calls: int,
) -> AgentPolicyViolation | None:
    """验证模型计划没有突破工具次数、资源范围和会议查询规则。"""

    if plan.expected_tool_calls > remaining_tool_calls:
        return AgentPolicyViolation(
            code="TOOL_CALL_LIMIT_EXCEEDED",
            message="执行计划超过本次工作流允许的工具调用次数",
        )

    if classification.action is EmailAction.IGNORE:
        disallowed = {
            PlanAction.READ_TOOL,
            PlanAction.GENERATE_DRAFT,
            PlanAction.REQUEST_CLARIFICATION,
        }
        if plan.should_generate_draft or any(step.action in disallowed for step in plan.steps):
            return AgentPolicyViolation(
                code="PLAN_POLICY_VIOLATION",
                message="忽略类邮件不能调用工具或生成草稿",
            )

    for step in plan.steps:
        if (
            step.tool_name is ReadOnlyToolName.GET_EMAIL_THREAD
            and isinstance(step.tool_arguments, GetEmailThreadArguments)
            and step.tool_arguments.thread_id != current_thread_id
        ):
            return AgentPolicyViolation(
                code="PLAN_RESOURCE_SCOPE_VIOLATION",
                message="邮件处理计划不能读取当前任务之外的邮件线程",
            )

    meeting = intent.meeting
    calendar_tools = {
        ReadOnlyToolName.CHECK_AVAILABILITY,
        ReadOnlyToolName.FIND_AVAILABLE_SLOTS,
    }
    if meeting.detected and not meeting.time_information_complete:
        if any(step.tool_name in calendar_tools for step in plan.steps):
            return AgentPolicyViolation(
                code="PLAN_POLICY_VIOLATION",
                message="会议时间信息不完整时不能根据猜测调用日历工具",
            )

    if (
        classification.action is not EmailAction.IGNORE
        and meeting.detected
        and meeting.time_information_complete
        and not _has_matching_availability_check(plan, intent)
    ):
        return AgentPolicyViolation(
            code="PLAN_POLICY_VIOLATION",
            message="时间完整的会议计划必须先使用准确时间检查日历可用性",
        )
    return None


def validate_draft_policy(
    *,
    draft: EmailDraft,
    sender: str,
    classification: EmailClassification,
    intent: ExtractedIntent,
) -> AgentPolicyViolation | None:
    """限制草稿收件人、抄送人和用途，阻止模型扩大外发范围。"""

    normalized_sender = sender.strip().lower()
    recipients = {str(address).lower() for address in draft.recipients}
    if not normalized_sender or recipients != {normalized_sender} or draft.cc:
        return AgentPolicyViolation(
            code="DRAFT_RECIPIENT_POLICY_VIOLATION",
            message="草稿只能发送给原邮件发件人，且当前版本不自动添加抄送人",
        )

    expected_purpose = DraftPurpose.REPLY
    if intent.needs_clarification:
        expected_purpose = DraftPurpose.CLARIFICATION
    elif classification.action is EmailAction.REMIND:
        expected_purpose = DraftPurpose.REMINDER
    if draft.purpose is not expected_purpose:
        return AgentPolicyViolation(
            code="DRAFT_PURPOSE_POLICY_VIOLATION",
            message="草稿用途与已校验的邮件处理决定不一致",
        )
    return None


def _has_matching_availability_check(
    plan: ExecutionPlan,
    intent: ExtractedIntent,
) -> bool:
    """确认计划使用意图节点提取出的准确时间查询用户日历。"""

    meeting = intent.meeting
    for step in plan.steps:
        if (
            step.action is PlanAction.READ_TOOL
            and step.tool_name is ReadOnlyToolName.CHECK_AVAILABILITY
            and isinstance(step.tool_arguments, CheckAvailabilityArguments)
            and step.tool_arguments.start_at == meeting.start_at
            and step.tool_arguments.end_at == meeting.end_at
        ):
            return True
    return False
