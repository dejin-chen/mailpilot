"""MailPilot 确定性工作流的纯路由函数。"""

from typing import Literal

from app.agent.schemas import AgentRunStatus, EmailAction, EmailClassification, ExecutionPlan
from app.agent.state import MailAgentState

NodeRoute = Literal["continue", "failed"]
ClassificationRoute = Literal["continue", "ignored", "failed"]
PlanRoute = Literal["execute_tools", "finalize", "failed"]
ToolRoute = Literal["finalize", "failed"]


def route_after_node(state: MailAgentState) -> NodeRoute:
    """普通节点只需要区分继续执行和失败收尾。"""

    if state.get("run_status") == AgentRunStatus.FAILED:
        return "failed"
    return "continue"


def route_after_classification(state: MailAgentState) -> ClassificationRoute:
    """分类失败时终止，忽略类邮件提前结束，其余邮件继续提取意图。"""

    if state.get("run_status") == AgentRunStatus.FAILED:
        return "failed"
    classification = state.get("classification")
    if not isinstance(classification, EmailClassification):
        return "failed"
    if classification.action is EmailAction.IGNORE:
        return "ignored"
    return "continue"


def route_after_plan(state: MailAgentState) -> PlanRoute:
    """计划中存在只读工具时进入执行节点，否则直接成功收尾。"""

    if state.get("run_status") == AgentRunStatus.FAILED:
        return "failed"
    plan = state.get("plan")
    if not isinstance(plan, ExecutionPlan):
        return "failed"
    if plan.expected_tool_calls > 0:
        return "execute_tools"
    return "finalize"


def route_after_tools(state: MailAgentState) -> ToolRoute:
    """工具调用只允许进入成功或失败收尾，不形成自由循环。"""

    if state.get("run_status") == AgentRunStatus.FAILED:
        return "failed"
    return "finalize"
