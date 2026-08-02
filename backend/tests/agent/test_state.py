"""Typed State、Reducer 和可信运行时 Context 测试。"""

from uuid import uuid4

import pytest
from app.agent.context import AgentRuntimeContext
from app.agent.schemas import AgentError, AgentRunStatus
from app.agent.state import MailAgentState, create_initial_state
from langgraph.graph import END, START, StateGraph


def test_create_initial_state_sets_safe_execution_defaults() -> None:
    email_thread_id = uuid4()

    state = create_initial_state(email_thread_id=email_thread_id)

    assert state["email_thread_id"] == email_thread_id
    assert state["tool_call_count"] == 0
    assert state["max_tool_calls"] == 4
    assert state["run_status"] is AgentRunStatus.PENDING
    assert "user_id" not in state


def test_create_initial_state_rejects_unbounded_tool_limit() -> None:
    with pytest.raises(ValueError, match="max_tool_calls"):
        create_initial_state(email_thread_id=uuid4(), max_tool_calls=11)


def test_state_error_reducer_appends_node_updates() -> None:
    def first_node(_: MailAgentState) -> dict[str, object]:
        return {"errors": [AgentError(node="first", code="FIRST", message="第一个测试错误")]}

    def second_node(_: MailAgentState) -> dict[str, object]:
        return {"errors": [AgentError(node="second", code="SECOND", message="第二个测试错误")]}

    builder = StateGraph(MailAgentState)
    builder.add_node("first", first_node)
    builder.add_node("second", second_node)
    builder.add_edge(START, "first")
    builder.add_edge("first", "second")
    builder.add_edge("second", END)
    graph = builder.compile()

    result = graph.invoke(create_initial_state(email_thread_id=uuid4()))

    assert [error.code for error in result["errors"]] == ["FIRST", "SECOND"]


def test_runtime_context_trims_request_id_and_rejects_blank_value() -> None:
    context = AgentRuntimeContext(
        user_id=uuid4(),
        agent_run_id=uuid4(),
        request_id="  request-001  ",
    )

    assert context.request_id == "request-001"

    with pytest.raises(ValueError, match="request_id"):
        AgentRuntimeContext(
            user_id=uuid4(),
            agent_run_id=uuid4(),
            request_id="   ",
        )


def test_runtime_context_rejects_invalid_user_timezone() -> None:
    with pytest.raises(ValueError, match="无效用户时区"):
        AgentRuntimeContext(
            user_id=uuid4(),
            agent_run_id=uuid4(),
            request_id="request-002",
            user_timezone="Mars/Office",
        )
