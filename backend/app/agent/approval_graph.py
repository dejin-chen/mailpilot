"""只负责危险写操作暂停与恢复的确定性审批闸门 Graph。"""

from typing import Literal

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from app.agent.approval_schemas import ApprovalGateStatus
from app.agent.approval_state import (
    ApprovalGateInput,
    ApprovalGateOutput,
    ApprovalGateState,
)
from app.agent.context import AgentRuntimeContext
from app.agent.nodes.approval import (
    ApprovalRequestCreator,
    PrepareApprovalNode,
    WaitForApprovalNode,
)
from app.agent.nodes.execute_write import (
    ApprovedActionExecutor,
    ExecuteApprovedActionNode,
)
from app.models.approval import ApprovalStatus

ApprovalGateGraph = CompiledStateGraph[
    ApprovalGateState,
    AgentRuntimeContext,
    ApprovalGateInput,
    ApprovalGateOutput,
]


def route_after_preparation(state: ApprovalGateState) -> Literal["wait", "failed"]:
    """审批单成功落库才允许进入 interrupt 等待节点。"""

    if state.get("gate_status") == ApprovalGateStatus.WAITING_APPROVAL:
        return "wait"
    return "failed"


def route_after_approval(
    state: ApprovalGateState,
) -> Literal["execute", "finish"]:
    """只有用户接受时调用写工具，拒绝和反馈都直接结束当前闸门。"""

    if state.get("approval_status") == ApprovalStatus.APPROVED:
        return "execute"
    return "finish"


def build_approval_gate_graph(
    *,
    creator: ApprovalRequestCreator,
    executor: ApprovedActionExecutor,
    checkpointer: BaseCheckpointSaver,
) -> ApprovalGateGraph:
    """编译必须带 Checkpointer 的审批闸门，禁止无存档暂停。"""

    builder = StateGraph(
        ApprovalGateState,
        context_schema=AgentRuntimeContext,
        input_schema=ApprovalGateInput,
        output_schema=ApprovalGateOutput,
    )
    builder.add_node("prepare_approval", PrepareApprovalNode(creator))
    builder.add_node("wait_for_approval", WaitForApprovalNode())
    builder.add_node(
        "execute_approved_action",
        ExecuteApprovedActionNode(executor),
    )
    builder.add_edge(START, "prepare_approval")
    builder.add_conditional_edges(
        "prepare_approval",
        route_after_preparation,
        {"wait": "wait_for_approval", "failed": END},
    )
    builder.add_conditional_edges(
        "wait_for_approval",
        route_after_approval,
        {"execute": "execute_approved_action", "finish": END},
    )
    builder.add_edge("execute_approved_action", END)
    return builder.compile(
        checkpointer=checkpointer,
        name="mailpilot_approval_gate",
    )
