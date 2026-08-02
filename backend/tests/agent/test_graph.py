"""MailPilot 确定性核心 Graph 完整路线测试。"""

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import pytest
from app.agent.context import AgentRuntimeContext
from app.agent.graph import build_mail_agent_graph
from app.agent.nodes.load_email import LoadedInboundEmail
from app.agent.schemas import (
    AgentRunStatus,
    EmailAction,
    EmailCategory,
    EmailClassification,
    EmailPriority,
    ExecutionPlan,
    ExtractedIntent,
    MeetingIntent,
    ModelUsage,
    PlanAction,
    PlanStep,
    ReadOnlyToolName,
)
from app.agent.state import create_initial_state
from app.integrations.llm.client import StructuredLlmResult
from app.integrations.llm.exceptions import LlmInvocationError
from langchain_core.messages import BaseMessage
from pydantic import BaseModel


class FakeEmailReader:
    """完整 Graph 测试使用的固定邮件读取器。"""

    def __init__(self) -> None:
        self.calls: list[tuple[UUID, UUID]] = []

    async def get_latest_inbound(
        self,
        *,
        user_id: UUID,
        thread_id: UUID,
    ) -> LoadedInboundEmail:
        self.calls.append((user_id, thread_id))
        return LoadedInboundEmail(
            message_id=uuid4(),
            subject="项目会议确认",
            body_text="请确认明天下午三点到四点是否可以开会。",
            sender="manager@example.com",
            recipients=("demo@example.com",),
            cc=(),
            sent_at=datetime(2026, 7, 23, 1, 0, tzinfo=UTC),
        )


class FakeLlmClient:
    """根据节点 operation 返回固定结果，便于验证 Graph 路线。"""

    def __init__(self, responses: dict[str, BaseModel | Exception]) -> None:
        self.responses = responses
        self.operations: list[str] = []

    async def ainvoke_structured[SchemaT: BaseModel](
        self,
        *,
        operation: str,
        messages: list[BaseMessage],
        schema: type[SchemaT],
    ) -> StructuredLlmResult[SchemaT]:
        del messages
        self.operations.append(operation)
        response = self.responses[operation]
        if isinstance(response, Exception):
            raise response
        return StructuredLlmResult(
            parsed=schema.model_validate(response.model_dump()),
            usage=ModelUsage(
                operation=operation,
                model_name="fake-model",
                input_tokens=10,
                output_tokens=5,
                total_tokens=15,
                latency_ms=1,
            ),
        )


class FakeToolClient:
    """记录 Graph 实际执行的 MCP 工具。"""

    def __init__(self, result: Any) -> None:
        self.result = result
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def invoke_tool(self, tool_name: str, arguments: dict[str, Any]) -> Any:
        self.calls.append((tool_name, arguments))
        return self.result


def _context() -> AgentRuntimeContext:
    return AgentRuntimeContext(
        user_id=uuid4(),
        agent_run_id=uuid4(),
        request_id="graph-test",
        user_timezone="Asia/Shanghai",
    )


def _classification(action: EmailAction) -> EmailClassification:
    return EmailClassification(
        action=action,
        priority=EmailPriority.NORMAL,
        category=EmailCategory.MEETING,
        summary="项目会议确认",
        reason="邮件要求确认会议",
        confidence=0.95,
    )


def _meeting_intent() -> ExtractedIntent:
    start_at = datetime(2026, 7, 24, 7, 0, tzinfo=UTC)
    return ExtractedIntent(
        meeting=MeetingIntent(
            detected=True,
            title="项目确认会",
            start_at=start_at,
            end_at=start_at + timedelta(hours=1),
            timezone="Asia/Shanghai",
            attendees=["manager@example.com"],
            time_information_complete=True,
            source_text="明天下午三点到四点",
        ),
        reason="会议时间完整",
    )


def _meeting_plan() -> ExecutionPlan:
    return ExecutionPlan(
        goal="检查会议时间并准备回复",
        steps=[
            PlanStep(
                sequence=1,
                action=PlanAction.READ_TOOL,
                description="检查日历",
                tool_name=ReadOnlyToolName.CHECK_AVAILABILITY,
                tool_arguments={
                    "start_at": "2026-07-24T07:00:00Z",
                    "end_at": "2026-07-24T08:00:00Z",
                },
            ),
            PlanStep(
                sequence=2,
                action=PlanAction.GENERATE_DRAFT,
                description="后续生成回复草稿",
            ),
        ],
        should_generate_draft=True,
    )


def _finalize_plan() -> ExecutionPlan:
    return ExecutionPlan(
        goal="完成普通邮件分析",
        steps=[
            PlanStep(
                sequence=1,
                action=PlanAction.FINALIZE,
                description="无需工具，直接结束",
            )
        ],
        should_generate_draft=False,
    )


@pytest.mark.asyncio
async def test_graph_runs_meeting_through_read_tool_to_success() -> None:
    context = _context()
    reader = FakeEmailReader()
    llm = FakeLlmClient(
        {
            "classify_email": _classification(EmailAction.REPLY),
            "extract_intent": _meeting_intent(),
            "build_plan": _meeting_plan(),
        }
    )
    tool_client = FakeToolClient({"available": False, "conflicts": [{"title": "周会"}]})
    graph = build_mail_agent_graph(
        reader=reader,
        llm_client=llm,
        tool_client_factory=lambda _: tool_client,
    )
    initial_state = create_initial_state(email_thread_id=uuid4())

    result = await graph.ainvoke(initial_state, context=context)

    assert reader.calls == [(context.user_id, initial_state["email_thread_id"])]
    assert llm.operations == ["classify_email", "extract_intent", "build_plan"]
    assert [call[0] for call in tool_client.calls] == ["check_availability"]
    assert result["run_status"] is AgentRunStatus.COMPLETED
    assert result["current_node"] == "finalize_success"
    assert result["tool_call_count"] == 1
    assert len(result["model_usages"]) == 3
    assert result["tool_results"][0].output == {
        "available": False,
        "conflicts": [{"title": "周会"}],
    }
    assert result["final_result"].status is AgentRunStatus.COMPLETED


@pytest.mark.asyncio
async def test_graph_ends_ignored_email_before_intent_and_tools() -> None:
    llm = FakeLlmClient({"classify_email": _classification(EmailAction.IGNORE)})
    tool_client = FakeToolClient({})
    graph = build_mail_agent_graph(
        reader=FakeEmailReader(),
        llm_client=llm,
        tool_client_factory=lambda _: tool_client,
    )

    result = await graph.ainvoke(
        create_initial_state(email_thread_id=uuid4()),
        context=_context(),
    )

    assert llm.operations == ["classify_email"]
    assert tool_client.calls == []
    assert result["run_status"] is AgentRunStatus.IGNORED


@pytest.mark.asyncio
async def test_graph_routes_model_failure_to_failed_end() -> None:
    llm = FakeLlmClient({"classify_email": LlmInvocationError("classify_email")})
    graph = build_mail_agent_graph(
        reader=FakeEmailReader(),
        llm_client=llm,
        tool_client_factory=lambda _: FakeToolClient({}),
    )

    result = await graph.ainvoke(
        create_initial_state(email_thread_id=uuid4()),
        context=_context(),
    )

    assert result["run_status"] is AgentRunStatus.FAILED
    assert result["errors"][0].code == "LLM_INVOCATION_ERROR"
    assert result["final_result"].status is AgentRunStatus.FAILED


@pytest.mark.asyncio
async def test_graph_skips_tool_node_when_plan_has_no_read_steps() -> None:
    no_meeting = ExtractedIntent(reason="普通邮件没有会议或任务")
    llm = FakeLlmClient(
        {
            "classify_email": _classification(EmailAction.REPLY),
            "extract_intent": no_meeting,
            "build_plan": _finalize_plan(),
        }
    )
    tool_client = FakeToolClient({})
    graph = build_mail_agent_graph(
        reader=FakeEmailReader(),
        llm_client=llm,
        tool_client_factory=lambda _: tool_client,
    )

    result = await graph.ainvoke(
        create_initial_state(email_thread_id=uuid4()),
        context=_context(),
    )

    assert tool_client.calls == []
    assert result["run_status"] is AgentRunStatus.COMPLETED
    assert result.get("tool_results", []) == []
