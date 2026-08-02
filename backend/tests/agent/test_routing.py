"""确定性条件路由和最终收尾节点测试。"""

from uuid import uuid4

from app.agent.nodes.finalize import FinalizeNode
from app.agent.routing import (
    route_after_classification,
    route_after_node,
    route_after_plan,
    route_after_tools,
)
from app.agent.schemas import (
    AgentError,
    AgentRunStatus,
    EmailAction,
    EmailCategory,
    EmailClassification,
    EmailPriority,
    ExecutionPlan,
    PlanAction,
    PlanStep,
    ReadOnlyToolName,
)
from app.agent.state import MailAgentState, create_initial_state


def _classification(action: EmailAction) -> EmailClassification:
    return EmailClassification(
        action=action,
        priority=EmailPriority.NORMAL,
        category=EmailCategory.MEETING,
        summary="测试邮件",
        reason="用于测试路由",
        confidence=0.9,
    )


def _plan(*, with_tool: bool) -> ExecutionPlan:
    if with_tool:
        steps = [
            PlanStep(
                sequence=1,
                action=PlanAction.READ_TOOL,
                description="检查日历",
                tool_name=ReadOnlyToolName.CHECK_AVAILABILITY,
                tool_arguments={
                    "start_at": "2026-07-24T07:00:00Z",
                    "end_at": "2026-07-24T08:00:00Z",
                },
            )
        ]
    else:
        steps = [
            PlanStep(
                sequence=1,
                action=PlanAction.FINALIZE,
                description="直接结束",
            )
        ]
    return ExecutionPlan(
        goal="测试计划",
        steps=steps,
        should_generate_draft=False,
    )


def test_routes_distinguish_failure_ignore_and_tool_execution() -> None:
    state = create_initial_state(email_thread_id=uuid4())
    state["run_status"] = AgentRunStatus.RUNNING

    assert route_after_node(state) == "continue"
    state["classification"] = _classification(EmailAction.IGNORE)
    assert route_after_classification(state) == "ignored"
    state["classification"] = _classification(EmailAction.REPLY)
    assert route_after_classification(state) == "continue"

    state["plan"] = _plan(with_tool=True)
    assert route_after_plan(state) == "execute_tools"
    state["plan"] = _plan(with_tool=False)
    assert route_after_plan(state) == "finalize"
    assert route_after_tools(state) == "finalize"

    state["run_status"] = AgentRunStatus.FAILED
    assert route_after_node(state) == "failed"
    assert route_after_classification(state) == "failed"
    assert route_after_plan(state) == "failed"
    assert route_after_tools(state) == "failed"


def test_finalize_nodes_create_stable_results() -> None:
    ignored_state = create_initial_state(email_thread_id=uuid4())
    ignored_state["classification"] = _classification(EmailAction.IGNORE)
    ignored = FinalizeNode("ignored")(ignored_state)

    assert ignored["run_status"] is AgentRunStatus.IGNORED
    assert ignored["final_result"].status is AgentRunStatus.IGNORED  # type: ignore[union-attr]

    failed_state: MailAgentState = create_initial_state(email_thread_id=uuid4())
    failed_state["errors"] = [AgentError(node="test", code="TEST_ERROR", message="测试失败")]
    failed = FinalizeNode("failed")(failed_state)

    assert failed["run_status"] is AgentRunStatus.FAILED
    assert "测试失败" in failed["final_result"].summary  # type: ignore[union-attr]

    success = FinalizeNode("success")(create_initial_state(email_thread_id=uuid4()))
    assert success["run_status"] is AgentRunStatus.COMPLETED
