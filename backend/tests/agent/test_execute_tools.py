"""受控只读 MCP 工具执行节点测试。"""

import json
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

import pytest
from app.agent.context import AgentRuntimeContext
from app.agent.nodes.execute_tools import ExecuteReadToolsNode
from app.agent.schemas import (
    AgentRunStatus,
    ExecutionPlan,
    PlanAction,
    PlanStep,
    ReadOnlyToolName,
)
from app.agent.state import MailAgentState, create_initial_state
from app.integrations.mcp.exceptions import McpToolInvocationError
from langgraph.runtime import Runtime


class FakeToolClient:
    """按顺序返回结果或抛出异常，并记录准确工具参数。"""

    def __init__(self, responses: list[Any]) -> None:
        self.responses = responses
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def invoke_tool(self, tool_name: str, arguments: dict[str, Any]) -> Any:
        self.calls.append((tool_name, arguments))
        response = self.responses[len(self.calls) - 1]
        if isinstance(response, Exception):
            raise response
        return response


def _context() -> AgentRuntimeContext:
    return AgentRuntimeContext(
        user_id=uuid4(),
        agent_run_id=uuid4(),
        request_id="execute-tools-test",
        user_timezone="Asia/Shanghai",
    )


def _tool_state(*, two_steps: bool = False) -> MailAgentState:
    state = create_initial_state(email_thread_id=uuid4())
    steps = [
        PlanStep(
            sequence=1,
            action=PlanAction.READ_TOOL,
            description="检查日历",
            tool_name=ReadOnlyToolName.CHECK_AVAILABILITY,
            tool_arguments={
                "start_at": datetime(2026, 7, 24, 7, 0, tzinfo=UTC),
                "end_at": datetime(2026, 7, 24, 8, 0, tzinfo=UTC),
            },
        )
    ]
    if two_steps:
        steps.append(
            PlanStep(
                sequence=2,
                action=PlanAction.READ_TOOL,
                description="搜索邮件",
                tool_name=ReadOnlyToolName.SEARCH_EMAILS,
                tool_arguments={"query": "项目确认会", "limit": 5},
            )
        )
    state["plan"] = ExecutionPlan(
        goal="查询相关业务数据",
        steps=steps,
        should_generate_draft=False,
    )
    state["run_status"] = AgentRunStatus.RUNNING
    return state


@pytest.mark.asyncio
async def test_execute_tools_uses_runtime_factory_and_exact_json_arguments() -> None:
    state = _tool_state(two_steps=True)
    client = FakeToolClient(
        [
            {"available": True, "conflicts": []},
            [{"type": "text", "text": json.dumps({"total": 1, "items": []})}],
        ]
    )
    captured_contexts: list[AgentRuntimeContext] = []

    def factory(context: AgentRuntimeContext) -> FakeToolClient:
        captured_contexts.append(context)
        return client

    context = _context()
    update = await ExecuteReadToolsNode(factory)(state, Runtime(context=context))

    assert captured_contexts == [context]
    assert [call[0] for call in client.calls] == [
        "check_availability",
        "search_emails",
    ]
    assert client.calls[0][1] == {
        "start_at": "2026-07-24T07:00:00Z",
        "end_at": "2026-07-24T08:00:00Z",
    }
    assert update["tool_call_count"] == 2
    assert update["run_status"] is AgentRunStatus.RUNNING
    results = update["tool_results"]
    assert isinstance(results, list)
    assert [result.success for result in results] == [True, True]
    assert results[1].output == {"total": 1, "items": []}


@pytest.mark.asyncio
async def test_execute_tools_stops_after_client_exception_and_records_attempt() -> None:
    state = _tool_state(two_steps=True)
    client = FakeToolClient([McpToolInvocationError("check_availability")])

    update = await ExecuteReadToolsNode(lambda _: client)(
        state,
        Runtime(context=_context()),
    )

    assert len(client.calls) == 1
    assert update["tool_call_count"] == 1
    assert update["run_status"] is AgentRunStatus.FAILED
    results = update["tool_results"]
    assert isinstance(results, list)
    assert results[0].success is False
    assert results[0].error_code == "MCP_TOOL_INVOCATION_FAILED"


@pytest.mark.asyncio
async def test_execute_tools_recognizes_mcp_error_content() -> None:
    state = _tool_state()
    error_payload = {
        "success": False,
        "error": {"code": "CALENDAR_UNAVAILABLE", "message": "日历暂时不可用"},
    }
    client = FakeToolClient(
        [[{"type": "text", "text": json.dumps(error_payload, ensure_ascii=False)}]]
    )

    update = await ExecuteReadToolsNode(lambda _: client)(
        state,
        Runtime(context=_context()),
    )

    assert update["run_status"] is AgentRunStatus.FAILED
    errors = update["errors"]
    assert isinstance(errors, list)
    assert errors[0].code == "CALENDAR_UNAVAILABLE"


@pytest.mark.asyncio
async def test_execute_tools_enforces_remaining_limit_before_network_call() -> None:
    state = _tool_state()
    state["tool_call_count"] = state["max_tool_calls"]
    client = FakeToolClient([])

    update = await ExecuteReadToolsNode(lambda _: client)(
        state,
        Runtime(context=_context()),
    )

    assert client.calls == []
    assert update["run_status"] is AgentRunStatus.FAILED
    errors = update["errors"]
    assert isinstance(errors, list)
    assert errors[0].code == "TOOL_CALL_LIMIT_EXCEEDED"
