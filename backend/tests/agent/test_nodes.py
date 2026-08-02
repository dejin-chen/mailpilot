"""4.2 邮件读取、分析和计划节点测试。"""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from app.agent.context import AgentRuntimeContext
from app.agent.nodes.classify import ClassifyEmailNode
from app.agent.nodes.extract_intent import ExtractIntentNode
from app.agent.nodes.load_email import (
    LoadedInboundEmail,
    LoadEmailNode,
    ServiceEmailThreadReader,
)
from app.agent.nodes.plan import BuildPlanNode
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
from app.agent.state import MailAgentState, create_initial_state
from app.integrations.llm.client import StructuredLlmResult
from app.integrations.llm.exceptions import LlmInvocationError
from app.models.email import EmailDirection
from langchain_core.messages import BaseMessage
from langgraph.runtime import Runtime
from pydantic import BaseModel


class FakeEmailReader:
    """记录可信身份并返回固定邮件快照。"""

    def __init__(self, email: LoadedInboundEmail) -> None:
        self.email = email
        self.calls: list[tuple[UUID, UUID]] = []

    async def get_latest_inbound(
        self,
        *,
        user_id: UUID,
        thread_id: UUID,
    ) -> LoadedInboundEmail:
        self.calls.append((user_id, thread_id))
        return self.email


class FakeLlmClient:
    """按 operation 返回预设 Pydantic 对象或异常。"""

    def __init__(self, responses: dict[str, BaseModel | Exception]) -> None:
        self.responses = responses
        self.calls: list[dict[str, object]] = []

    async def ainvoke_structured[SchemaT: BaseModel](
        self,
        *,
        operation: str,
        messages: list[BaseMessage],
        schema: type[SchemaT],
    ) -> StructuredLlmResult[SchemaT]:
        self.calls.append({"operation": operation, "messages": messages, "schema": schema})
        response = self.responses[operation]
        if isinstance(response, Exception):
            raise response
        parsed = schema.model_validate(response.model_dump())
        return StructuredLlmResult(
            parsed=parsed,
            usage=ModelUsage(
                operation=operation,
                model_name="fake-model",
                input_tokens=10,
                output_tokens=5,
                total_tokens=15,
                latency_ms=1,
            ),
        )


def _context() -> AgentRuntimeContext:
    return AgentRuntimeContext(
        user_id=uuid4(),
        agent_run_id=uuid4(),
        request_id="agent-node-test",
        user_timezone="Asia/Shanghai",
    )


def _loaded_email() -> LoadedInboundEmail:
    return LoadedInboundEmail(
        message_id=uuid4(),
        subject="项目确认会",
        body_text="请确认明天下午三点是否可以开会。",
        sender="manager@example.com",
        recipients=("demo@example.com",),
        cc=(),
        sent_at=datetime(2026, 7, 23, 2, 0, tzinfo=UTC),
    )


def _loaded_state() -> MailAgentState:
    email = _loaded_email()
    state = create_initial_state(email_thread_id=uuid4())
    state.update(
        {
            "email_message_id": email.message_id,
            "email_subject": email.subject,
            "email_body": email.body_text,
            "sender": email.sender,
            "recipients": list(email.recipients),
            "cc": list(email.cc),
            "email_sent_at": email.sent_at,
        }
    )
    return state


def _classification() -> EmailClassification:
    return EmailClassification(
        action=EmailAction.REPLY,
        priority=EmailPriority.HIGH,
        category=EmailCategory.MEETING,
        summary="需要确认会议时间",
        reason="发件人明确要求回复",
        confidence=0.94,
    )


def _intent() -> ExtractedIntent:
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
            source_text="明天下午三点",
        ),
        reason="邮件包含明确会议时间",
    )


def _plan(tool_steps: int = 1) -> ExecutionPlan:
    steps = [
        PlanStep(
            sequence=index + 1,
            action=PlanAction.READ_TOOL,
            description=f"第 {index + 1} 次检查日历",
            tool_name=ReadOnlyToolName.CHECK_AVAILABILITY,
            tool_arguments={
                "start_at": "2026-07-24T07:00:00Z",
                "end_at": "2026-07-24T08:00:00Z",
            },
        )
        for index in range(tool_steps)
    ]
    steps.append(
        PlanStep(
            sequence=len(steps) + 1,
            action=PlanAction.GENERATE_DRAFT,
            description="生成回复草稿",
        )
    )
    return ExecutionPlan(
        goal="确认会议时间并准备草稿",
        steps=steps,
        should_generate_draft=True,
    )


@pytest.mark.asyncio
async def test_load_email_node_uses_runtime_user_and_updates_raw_state() -> None:
    context = _context()
    email = _loaded_email()
    reader = FakeEmailReader(email)
    state = create_initial_state(email_thread_id=uuid4())

    update = await LoadEmailNode(reader)(state, Runtime(context=context))

    assert reader.calls == [(context.user_id, state["email_thread_id"])]
    assert update["email_message_id"] == email.message_id
    assert update["email_body"] == email.body_text
    assert update["run_status"] is AgentRunStatus.RUNNING


@pytest.mark.asyncio
async def test_service_reader_chooses_latest_inbound_not_newer_draft(monkeypatch) -> None:
    older_inbound = SimpleNamespace(
        id=uuid4(),
        direction=EmailDirection.INBOUND,
        subject="较早收件",
        body_text="第一封",
        sender="first@example.com",
        recipients=["demo@example.com"],
        cc=[],
        sent_at=datetime(2026, 7, 23, 1, 0, tzinfo=UTC),
    )
    latest_inbound = SimpleNamespace(
        id=uuid4(),
        direction=EmailDirection.INBOUND,
        subject="最新收件",
        body_text="第二封",
        sender="second@example.com",
        recipients=["demo@example.com"],
        cc=[],
        sent_at=datetime(2026, 7, 23, 2, 0, tzinfo=UTC),
    )
    newer_draft = SimpleNamespace(
        id=uuid4(),
        direction=EmailDirection.DRAFT,
        subject="系统草稿",
        body_text="不应被分析",
        sender="demo@example.com",
        recipients=["second@example.com"],
        cc=[],
        sent_at=datetime(2026, 7, 23, 3, 0, tzinfo=UTC),
    )
    thread = SimpleNamespace(messages=[older_inbound, latest_inbound, newer_draft])

    class FakeSessionContext:
        async def __aenter__(self) -> object:
            return object()

        async def __aexit__(self, *args: object) -> None:
            del args

    class FakeEmailService:
        def __init__(self, session: object) -> None:
            del session

        async def get_thread(self, **kwargs: object) -> object:
            del kwargs
            return thread

    monkeypatch.setattr("app.agent.nodes.load_email.EmailService", FakeEmailService)
    reader = ServiceEmailThreadReader(session_factory=FakeSessionContext)

    email = await reader.get_latest_inbound(user_id=uuid4(), thread_id=uuid4())

    assert email.message_id == latest_inbound.id
    assert email.subject == "最新收件"


@pytest.mark.asyncio
async def test_analysis_nodes_write_structured_results_and_usage() -> None:
    state = _loaded_state()
    llm = FakeLlmClient(
        {
            "classify_email": _classification(),
            "extract_intent": _intent(),
            "build_plan": _plan(),
        }
    )

    classification_update = await ClassifyEmailNode(llm)(state)
    state.update(classification_update)  # type: ignore[typeddict-item]
    intent_update = await ExtractIntentNode(llm)(state, Runtime(context=_context()))
    state.update(intent_update)  # type: ignore[typeddict-item]
    plan_update = await BuildPlanNode(llm)(state)

    assert classification_update["classification"] == _classification()
    assert intent_update["intent"] == _intent()
    assert plan_update["plan"] == _plan()
    assert [call["operation"] for call in llm.calls] == [
        "classify_email",
        "extract_intent",
        "build_plan",
    ]
    assert len(classification_update["model_usages"]) == 1  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_model_failure_becomes_safe_failed_state_update() -> None:
    llm = FakeLlmClient({"classify_email": LlmInvocationError("classify_email")})

    update = await ClassifyEmailNode(llm)(_loaded_state())

    assert update["run_status"] is AgentRunStatus.FAILED
    errors = update["errors"]
    assert isinstance(errors, list)
    assert errors[0].code == "LLM_INVOCATION_ERROR"
    assert errors[0].retryable is True


@pytest.mark.asyncio
async def test_plan_node_rejects_more_tools_than_remaining_limit() -> None:
    state = _loaded_state()
    state["classification"] = _classification()
    state["intent"] = _intent()
    state["max_tool_calls"] = 2
    state["tool_call_count"] = 1
    llm = FakeLlmClient({"build_plan": _plan(tool_steps=2)})

    update = await BuildPlanNode(llm)(state)

    assert update["run_status"] is AgentRunStatus.FAILED
    errors = update["errors"]
    assert isinstance(errors, list)
    assert errors[0].code == "TOOL_CALL_LIMIT_EXCEEDED"


@pytest.mark.asyncio
async def test_plan_node_requires_availability_check_for_complete_meeting() -> None:
    state = _loaded_state()
    state["classification"] = _classification()
    state["intent"] = _intent()
    unsafe_plan = ExecutionPlan(
        goal="直接生成会议回复",
        steps=[
            PlanStep(
                sequence=1,
                action=PlanAction.GENERATE_DRAFT,
                description="生成回复草稿",
            )
        ],
        should_generate_draft=True,
    )
    llm = FakeLlmClient({"build_plan": unsafe_plan})

    update = await BuildPlanNode(llm)(state)

    assert update["run_status"] is AgentRunStatus.FAILED
    errors = update["errors"]
    assert isinstance(errors, list)
    assert errors[0].code == "PLAN_POLICY_VIOLATION"
