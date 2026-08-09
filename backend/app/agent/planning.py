"""把模型的精简决策转换为可审查、可执行的确定性计划。"""

from app.agent.schemas import (
    CheckAvailabilityArguments,
    EmailAction,
    EmailClassification,
    ExecutionPlan,
    IntentPlanDecision,
    PlanAction,
    PlanStep,
    ReadOnlyToolName,
    ReadToolDecision,
)

_TOOL_DESCRIPTIONS: dict[ReadOnlyToolName, str] = {
    ReadOnlyToolName.GET_EMAIL_THREAD: "读取当前邮件线程上下文",
    ReadOnlyToolName.SEARCH_EMAILS: "搜索完成当前请求所需的历史邮件",
    ReadOnlyToolName.CHECK_AVAILABILITY: "按邮件中的准确时间检查日历冲突",
    ReadOnlyToolName.FIND_AVAILABLE_SLOTS: "在指定窗口中查询可用会议时段",
}


def build_execution_plan(
    *,
    decision: IntentPlanDecision,
    classification: EmailClassification,
) -> ExecutionPlan:
    """将模型负责的语义判断转换为由 Python 保证约束的执行计划。

    模型仍负责理解邮件、提取时间和选择按需工具；Python 负责步骤编号、
    完整会议必须查日历、信息不完整不得猜时间，以及 reply 必须生成草稿。
    """

    intent = decision.intent
    read_tools = list(decision.read_tools)
    meeting = intent.meeting
    if meeting.detected and meeting.time_information_complete:
        # 时间完整时，不信任模型自行复制的时间参数，直接使用已通过 Schema
        # 校验的意图时间，避免计划节点二次抄写造成偏差。
        read_tools = [
            item
            for item in read_tools
            if item.tool_name is not ReadOnlyToolName.CHECK_AVAILABILITY
        ][:3]
        read_tools.insert(
            0,
            ReadToolDecision(
                tool_name=ReadOnlyToolName.CHECK_AVAILABILITY,
                tool_arguments=CheckAvailabilityArguments(
                    start_at=meeting.start_at,
                    end_at=meeting.end_at,
                ),
            ),
        )
    elif meeting.detected:
        # 时间缺失时不能让邮件正文或模型猜测日历查询参数。
        read_tools = [
            item
            for item in read_tools
            if item.tool_name
            not in {
                ReadOnlyToolName.CHECK_AVAILABILITY,
                ReadOnlyToolName.FIND_AVAILABLE_SLOTS,
            }
        ]

    should_generate_draft = bool(
        decision.should_generate_draft
        or classification.action is EmailAction.REPLY
        or intent.needs_clarification
    )
    steps = [
        PlanStep(
            sequence=index,
            action=PlanAction.READ_TOOL,
            description=_TOOL_DESCRIPTIONS[item.tool_name],
            tool_name=item.tool_name,
            tool_arguments=item.tool_arguments,
        )
        for index, item in enumerate(read_tools, start=1)
    ]
    terminal_action = PlanAction.FINALIZE
    terminal_description = "完成邮件处理并记录结果"
    if intent.needs_clarification:
        terminal_action = PlanAction.REQUEST_CLARIFICATION
        terminal_description = "生成只询问缺失信息的澄清草稿"
    elif should_generate_draft:
        terminal_action = PlanAction.GENERATE_DRAFT
        terminal_description = "生成待人工审批的回复草稿"
    steps.append(
        PlanStep(
            sequence=len(steps) + 1,
            action=terminal_action,
            description=terminal_description,
        )
    )

    if intent.needs_clarification:
        goal = "补齐缺失信息并生成澄清草稿"
    elif meeting.detected and meeting.time_information_complete:
        goal = "检查日历并形成受控的会议处理方案"
    elif should_generate_draft:
        goal = "获取必要信息并生成待审批回复草稿"
    else:
        goal = "获取必要信息并完成邮件处理"
    return ExecutionPlan(
        goal=goal,
        steps=steps,
        should_generate_draft=should_generate_draft,
        needs_clarification=intent.needs_clarification,
    )
