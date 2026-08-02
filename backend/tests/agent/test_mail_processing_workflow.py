"""完整邮件处理 Graph 的暂停、恢复和反馈重生成测试。"""

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import pytest
from app.agent.approval_schemas import (
    ApprovalGateStatus,
    WriteActionExecution,
    WriteExecutionStatus,
)
from app.agent.context import AgentRuntimeContext
from app.agent.nodes.approval import PreparedApproval
from app.agent.nodes.execute_write import ExecutedApprovedAction
from app.agent.nodes.load_email import LoadedInboundEmail
from app.agent.schemas import (
    DraftPurpose,
    EmailAction,
    EmailCategory,
    EmailClassification,
    EmailDraft,
    EmailPriority,
    ExecutionPlan,
    ExtractedIntent,
    MeetingIntent,
    ModelUsage,
    PlanAction,
    PlanStep,
    ReadOnlyToolName,
)
from app.agent.workflow import build_mail_processing_graph
from app.integrations.llm.client import StructuredLlmResult
from app.memory.schemas import StoredMemoryEntry
from app.memory.store import memory_store_namespace
from app.models.approval import ApprovalAction, ApprovalStatus
from app.models.memory import MemoryType
from app.schemas.memory_feedback import (
    CalendarPreferencesMemoryUpdateProposal,
    ContactMemoryUpdateProposal,
    EmailStyleMemoryUpdateProposal,
    MemoryFeedbackUpdateResult,
)
from langchain_core.messages import BaseMessage
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.store.memory import InMemoryStore
from langgraph.types import Command
from pydantic import BaseModel


class FakeReader:
    async def get_latest_inbound(
        self,
        *,
        user_id: UUID,
        thread_id: UUID,
    ) -> LoadedInboundEmail:
        del user_id, thread_id
        return LoadedInboundEmail(
            message_id=uuid4(),
            subject="请确认项目进度",
            body_text="请回复确认今天能否完成。",
            sender="manager@example.com",
            recipients=("user@example.com",),
            cc=(),
            sent_at=datetime(2026, 7, 24, 2, 0, tzinfo=UTC),
        )


class FakeLlm:
    def __init__(self) -> None:
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
        responses: dict[str, BaseModel] = {
            "classify_email": EmailClassification(
                action=EmailAction.REPLY,
                priority=EmailPriority.NORMAL,
                category=EmailCategory.REQUEST,
                summary="需要确认进度",
                reason="发件人明确要求回复",
                confidence=0.95,
            ),
            "extract_intent": ExtractedIntent(reason="没有会议意图"),
            "build_plan": ExecutionPlan(
                goal="生成确认回复",
                steps=[
                    PlanStep(
                        sequence=1,
                        action=PlanAction.GENERATE_DRAFT,
                        description="生成回复草稿",
                    )
                ],
                should_generate_draft=True,
            ),
            "generate_draft": EmailDraft(
                purpose=DraftPurpose.REPLY,
                recipients=["manager@example.com"],
                subject="Re: 请确认项目进度",
                body_text="您好，项目可以按计划完成。",
            ),
        }
        parsed = responses[operation]
        return StructuredLlmResult(
            parsed=schema.model_validate(parsed.model_dump(mode="json")),
            usage=ModelUsage(
                operation=operation,
                model_name="fake-model",
                input_tokens=10,
                output_tokens=5,
                total_tokens=15,
                latency_ms=1,
            ),
        )


class FakeMeetingLlm(FakeLlm):
    async def ainvoke_structured[SchemaT: BaseModel](
        self,
        *,
        operation: str,
        messages: list[BaseMessage],
        schema: type[SchemaT],
    ) -> StructuredLlmResult[SchemaT]:
        del messages
        self.operations.append(operation)
        start_at = datetime(2026, 7, 25, 7, 0, tzinfo=UTC)
        responses: dict[str, BaseModel] = {
            "classify_email": EmailClassification(
                action=EmailAction.REPLY,
                priority=EmailPriority.NORMAL,
                category=EmailCategory.MEETING,
                summary="会议邀请",
                reason="时间信息完整",
                confidence=0.95,
            ),
            "extract_intent": ExtractedIntent(
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
            ),
            "build_plan": ExecutionPlan(
                goal="检查时间并安排会议",
                steps=[
                    PlanStep(
                        sequence=1,
                        action=PlanAction.READ_TOOL,
                        description="检查日历冲突",
                        tool_name=ReadOnlyToolName.CHECK_AVAILABILITY,
                        tool_arguments={
                            "start_at": start_at.isoformat(),
                            "end_at": (start_at + timedelta(hours=1)).isoformat(),
                        },
                    ),
                    PlanStep(
                        sequence=2,
                        action=PlanAction.GENERATE_DRAFT,
                        description="冲突时生成回复草稿",
                    ),
                ],
                should_generate_draft=True,
            ),
        }
        parsed = responses[operation]
        return StructuredLlmResult(
            parsed=schema.model_validate(parsed.model_dump(mode="json")),
            usage=ModelUsage(
                operation=operation,
                model_name="fake-model",
                input_tokens=10,
                output_tokens=5,
                total_tokens=15,
                latency_ms=1,
            ),
        )


class FakeMaliciousRecipientLlm(FakeLlm):
    async def ainvoke_structured[SchemaT: BaseModel](
        self,
        *,
        operation: str,
        messages: list[BaseMessage],
        schema: type[SchemaT],
    ) -> StructuredLlmResult[SchemaT]:
        if operation != "generate_draft":
            return await super().ainvoke_structured(
                operation=operation,
                messages=messages,
                schema=schema,
            )
        self.operations.append(operation)
        return StructuredLlmResult(
            parsed=schema.model_validate(
                EmailDraft(
                    purpose=DraftPurpose.REPLY,
                    recipients=["attacker@example.com"],
                    subject="敏感数据",
                    body_text="请查看内部数据。",
                ).model_dump(mode="json")
            ),
            usage=ModelUsage(
                operation=operation,
                model_name="fake-model",
                input_tokens=10,
                output_tokens=5,
                total_tokens=15,
                latency_ms=1,
            ),
        )


class FakeDraftClient:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def invoke_tool(self, tool_name: str, arguments: dict[str, Any]) -> Any:
        self.calls.append((tool_name, arguments))
        now = datetime(2026, 7, 24, 3, 0, tzinfo=UTC).isoformat()
        return {
            "message": {
                "id": str(uuid4()),
                "thread_id": arguments["thread_id"],
                "provider": "local",
                "external_id": f"draft-{len(self.calls)}",
                "subject": arguments["subject"],
                "sender": "user@example.com",
                "recipients": arguments["recipients"],
                "cc": arguments["cc"],
                "body_text": arguments["body_text"],
                "direction": "draft",
                "headers": {},
                "sent_at": now,
                "idempotency_key": arguments["idempotency_key"],
                "created_at": now,
                "updated_at": now,
            },
            "reused": False,
        }


class FakeAvailableCalendarClient:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def invoke_tool(self, tool_name: str, arguments: dict[str, Any]) -> Any:
        self.calls.append((tool_name, arguments))
        return {"available": True, "conflicts": []}


class FakeApprovalCreator:
    def __init__(self) -> None:
        self.ids: list[UUID] = []

    async def create(self, **_: object) -> PreparedApproval:
        approval_id = uuid4()
        self.ids.append(approval_id)
        return PreparedApproval(
            approval_request_id=approval_id,
            status=ApprovalStatus.PENDING,
        )


class FakeExecutor:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    async def execute(self, **kwargs: object) -> ExecutedApprovedAction:
        self.calls.append(kwargs)
        return ExecutedApprovedAction(
            execution=WriteActionExecution(
                action=ApprovalAction.SEND_EMAIL,
                tool_name="send_email",
                idempotency_key="write-full-workflow-v1",
                arguments={"draft_message_id": str(uuid4())},
                result={"success": True},
            )
        )


class FakeMemoryFeedbackUpdater:
    """普通反馈测试不会调用它；长期反馈测试可检查调用参数。"""

    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    async def apply(
        self,
        **kwargs: object,
    ) -> MemoryFeedbackUpdateResult:
        self.calls.append(kwargs)
        proposal = kwargs["proposal"]
        assert isinstance(
            proposal,
            (
                EmailStyleMemoryUpdateProposal,
                CalendarPreferencesMemoryUpdateProposal,
                ContactMemoryUpdateProposal,
            ),
        )
        return MemoryFeedbackUpdateResult(
            applied=True,
            reused=False,
            memory_id=uuid4(),
            memory_type=proposal.memory_type,
            memory_key=str(proposal.memory_key),
            version=2,
        )


def _context() -> AgentRuntimeContext:
    return AgentRuntimeContext(
        user_id=uuid4(),
        agent_run_id=uuid4(),
        request_id="full-workflow-test",
        user_timezone="Asia/Shanghai",
    )


def _build_graph(store: InMemoryStore | None = None):
    llm = FakeLlm()
    draft_client = FakeDraftClient()
    approvals = FakeApprovalCreator()
    executor = FakeExecutor()
    graph = build_mail_processing_graph(
        reader=FakeReader(),
        llm_client=llm,
        read_tool_client_factory=lambda _: draft_client,
        safe_tool_client_factory=lambda _: draft_client,
        approval_creator=approvals,
        memory_feedback_updater=FakeMemoryFeedbackUpdater(),
        executor=executor,
        checkpointer=InMemorySaver(),
        store=store or InMemoryStore(),
    )
    return graph, llm, draft_client, approvals, executor


@pytest.mark.asyncio
async def test_long_term_memory_is_shared_across_threads_but_not_users() -> None:
    store = InMemoryStore()
    owner_context = _context()
    memory = StoredMemoryEntry(
        memory_id=uuid4(),
        memory_type=MemoryType.EMAIL_STYLE,
        memory_key="default",
        version=3,
        value={"tone": "简洁专业", "signature": "张三｜研发部"},
        updated_at=datetime(2026, 7, 25, 1, 0, tzinfo=UTC),
    )
    await store.aput(
        memory_store_namespace(
            user_id=owner_context.user_id,
            memory_type=MemoryType.EMAIL_STYLE,
        ),
        "default",
        memory.model_dump(mode="json"),
    )
    graph, _, _, _, _ = _build_graph(store)

    first = await graph.ainvoke(
        {"email_thread_id": uuid4()},
        config={"configurable": {"thread_id": f"memory-first-{uuid4()}"}},
        context=owner_context,
    )
    second = await graph.ainvoke(
        {"email_thread_id": uuid4()},
        config={"configurable": {"thread_id": f"memory-second-{uuid4()}"}},
        context=owner_context,
    )
    stranger = await graph.ainvoke(
        {"email_thread_id": uuid4()},
        config={"configurable": {"thread_id": f"memory-stranger-{uuid4()}"}},
        context=_context(),
    )

    assert first["memory_context"].email_style.tone == "简洁专业"
    assert second["memory_context"].email_style.tone == "简洁专业"
    assert first["memory_context"].references[0].version == 3
    assert stranger["memory_context"].email_style is None
    assert stranger["memory_context"].references == []


@pytest.mark.asyncio
async def test_full_workflow_pauses_then_executes_approved_email() -> None:
    graph, llm, draft_client, approvals, executor = _build_graph()
    context = _context()
    config = {"configurable": {"thread_id": f"mail-flow-{uuid4()}"}}

    paused = await graph.ainvoke(
        {"email_thread_id": uuid4()},
        config=config,
        context=context,
    )

    assert "__interrupt__" in paused
    assert paused["gate_status"] is ApprovalGateStatus.WAITING_APPROVAL
    assert paused["proposal"].action is ApprovalAction.SEND_EMAIL
    assert paused["proposal"].version == 1
    assert draft_client.calls[0][0] == "create_email_draft"
    assert llm.operations == [
        "classify_email",
        "extract_intent",
        "build_plan",
        "generate_draft",
    ]

    resumed = await graph.ainvoke(
        Command(
            resume={
                "approval_request_id": str(approvals.ids[0]),
                "status": "approved",
            }
        ),
        config=config,
        context=context,
    )

    assert resumed["run_status"].value == "completed"
    assert resumed["execution_status"] is WriteExecutionStatus.SUCCEEDED
    assert resumed["current_node"] == "finalize_success"
    assert len(executor.calls) == 1


@pytest.mark.asyncio
async def test_feedback_creates_versioned_draft_and_second_approval() -> None:
    graph, llm, draft_client, approvals, executor = _build_graph()
    context = _context()
    config = {"configurable": {"thread_id": f"mail-feedback-{uuid4()}"}}
    await graph.ainvoke(
        {"email_thread_id": uuid4()},
        config=config,
        context=context,
    )

    paused_again = await graph.ainvoke(
        Command(
            resume={
                "approval_request_id": str(approvals.ids[0]),
                "status": "feedback_requested",
                "feedback": "语气再简洁一点",
            }
        ),
        config=config,
        context=context,
    )

    assert "__interrupt__" in paused_again
    assert len(approvals.ids) == 2
    assert paused_again["approval_request_id"] == approvals.ids[1]
    assert paused_again["proposal"].version == 2
    assert len(draft_client.calls) == 2
    assert draft_client.calls[0][1]["idempotency_key"].endswith(":v1")
    assert draft_client.calls[1][1]["idempotency_key"].endswith(":v2")
    assert llm.operations.count("generate_draft") == 2
    assert executor.calls == []


@pytest.mark.asyncio
async def test_available_meeting_becomes_create_event_approval_without_draft() -> None:
    llm = FakeMeetingLlm()
    calendar_client = FakeAvailableCalendarClient()
    draft_client = FakeDraftClient()
    approvals = FakeApprovalCreator()
    graph = build_mail_processing_graph(
        reader=FakeReader(),
        llm_client=llm,
        read_tool_client_factory=lambda _: calendar_client,
        safe_tool_client_factory=lambda _: draft_client,
        approval_creator=approvals,
        memory_feedback_updater=FakeMemoryFeedbackUpdater(),
        executor=FakeExecutor(),
        checkpointer=InMemorySaver(),
        store=InMemoryStore(),
    )

    paused = await graph.ainvoke(
        {"email_thread_id": uuid4()},
        config={"configurable": {"thread_id": f"meeting-flow-{uuid4()}"}},
        context=_context(),
    )

    assert "__interrupt__" in paused
    assert paused["proposal"].action is ApprovalAction.CREATE_EVENT
    assert paused["proposal"].arguments.title == "项目确认会"
    assert calendar_client.calls[0][0] == "check_availability"
    assert draft_client.calls == []
    assert "generate_draft" not in llm.operations


@pytest.mark.asyncio
async def test_draft_recipient_policy_blocks_model_from_adding_attacker() -> None:
    draft_client = FakeDraftClient()
    approvals = FakeApprovalCreator()
    graph = build_mail_processing_graph(
        reader=FakeReader(),
        llm_client=FakeMaliciousRecipientLlm(),
        read_tool_client_factory=lambda _: draft_client,
        safe_tool_client_factory=lambda _: draft_client,
        approval_creator=approvals,
        memory_feedback_updater=FakeMemoryFeedbackUpdater(),
        executor=FakeExecutor(),
        checkpointer=InMemorySaver(),
        store=InMemoryStore(),
    )

    result = await graph.ainvoke(
        {"email_thread_id": uuid4()},
        config={"configurable": {"thread_id": f"recipient-policy-{uuid4()}"}},
        context=_context(),
    )

    assert result["run_status"].value == "failed"
    assert result["errors"][-1].code == "DRAFT_RECIPIENT_POLICY_VIOLATION"
    assert draft_client.calls == []
    assert approvals.ids == []
