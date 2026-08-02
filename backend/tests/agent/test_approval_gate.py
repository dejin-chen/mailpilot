"""审批准备与 interrupt 暂停、恢复的内存级 Graph 测试。"""

from uuid import UUID, uuid4

import pytest
from app.agent.approval_graph import build_approval_gate_graph
from app.agent.approval_schemas import (
    ApprovalGateStatus,
    SendEmailArguments,
    WriteActionExecution,
    WriteActionProposal,
    WriteExecutionStatus,
)
from app.agent.context import AgentRuntimeContext
from app.agent.exceptions import ApprovedActionExecutionError
from app.agent.nodes.approval import (
    PreparedApproval,
    build_approval_idempotency_key,
)
from app.agent.nodes.execute_write import ExecutedApprovedAction
from app.models.approval import ApprovalAction, ApprovalStatus
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command


class FakeApprovalCreator:
    """记录可信上下文是否原样传到审批 Service 边界。"""

    def __init__(self, approval_request_id: UUID | None = None) -> None:
        self.approval_request_id = approval_request_id or uuid4()
        self.calls: list[dict[str, object]] = []

    async def create(self, **kwargs: object) -> PreparedApproval:
        self.calls.append(kwargs)
        return PreparedApproval(
            approval_request_id=self.approval_request_id,
            status=ApprovalStatus.PENDING,
        )


class FakeApprovedActionExecutor:
    """模拟已经由 MCP 执行成功的邮件写操作。"""

    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    async def execute(self, **kwargs: object) -> ExecutedApprovedAction:
        self.calls.append(kwargs)
        return ExecutedApprovedAction(
            execution=WriteActionExecution(
                action=ApprovalAction.SEND_EMAIL,
                tool_name="send_email",
                idempotency_key="write-test-001",
                arguments={"draft_message_id": str(uuid4())},
                result={"success": True},
            )
        )


class UncertainApprovedActionExecutor:
    async def execute(self, **_: object) -> ExecutedApprovedAction:
        raise ApprovedActionExecutionError(
            code="MCP_TOOL_INVOCATION_FAILED",
            message="写工具结果暂时无法确定",
            uncertain=True,
        )


def _proposal() -> WriteActionProposal:
    return WriteActionProposal(
        action=ApprovalAction.SEND_EMAIL,
        arguments=SendEmailArguments(draft_message_id=uuid4()),
        summary="向项目经理发送确认邮件",
    )


def _context() -> AgentRuntimeContext:
    return AgentRuntimeContext(
        user_id=uuid4(),
        agent_run_id=uuid4(),
        request_id="approval-gate-test",
        user_timezone="Asia/Shanghai",
    )


@pytest.mark.asyncio
async def test_gate_pauses_then_resumes_without_recreating_approval() -> None:
    creator = FakeApprovalCreator()
    executor = FakeApprovedActionExecutor()
    graph = build_approval_gate_graph(
        creator=creator,
        executor=executor,
        checkpointer=InMemorySaver(),
    )
    context = _context()
    config = {"configurable": {"thread_id": f"gate-{uuid4()}"}}

    paused = await graph.ainvoke(
        {"proposal": _proposal()},
        config=config,
        context=context,
    )

    assert len(paused["__interrupt__"]) == 1
    interrupt_payload = paused["__interrupt__"][0].value
    assert interrupt_payload["approval_request_id"] == str(creator.approval_request_id)
    assert interrupt_payload["agent_run_id"] == str(context.agent_run_id)
    snapshot = await graph.aget_state(config)
    assert snapshot.values["gate_status"] is ApprovalGateStatus.WAITING_APPROVAL

    resumed = await graph.ainvoke(
        Command(
            resume={
                "approval_request_id": str(creator.approval_request_id),
                "status": "approved",
            }
        ),
        config=config,
        context=context,
    )

    assert resumed["gate_status"] is ApprovalGateStatus.RESUMED
    assert resumed["approval_status"] is ApprovalStatus.APPROVED
    assert resumed["execution_status"] is WriteExecutionStatus.SUCCEEDED
    assert len(creator.calls) == 1
    assert len(executor.calls) == 1
    assert creator.calls[0]["user_id"] == context.user_id
    assert creator.calls[0]["agent_run_id"] == context.agent_run_id


@pytest.mark.asyncio
async def test_rejected_gate_never_calls_write_executor() -> None:
    creator = FakeApprovalCreator()
    executor = FakeApprovedActionExecutor()
    graph = build_approval_gate_graph(
        creator=creator,
        executor=executor,
        checkpointer=InMemorySaver(),
    )
    context = _context()
    config = {"configurable": {"thread_id": f"gate-rejected-{uuid4()}"}}
    await graph.ainvoke({"proposal": _proposal()}, config=config, context=context)

    resumed = await graph.ainvoke(
        Command(
            resume={
                "approval_request_id": str(creator.approval_request_id),
                "status": "rejected",
            }
        ),
        config=config,
        context=context,
    )

    assert resumed["approval_status"] is ApprovalStatus.REJECTED
    assert "execution_status" not in resumed
    assert executor.calls == []


@pytest.mark.asyncio
async def test_transport_failure_becomes_uncertain_without_graph_retry() -> None:
    creator = FakeApprovalCreator()
    graph = build_approval_gate_graph(
        creator=creator,
        executor=UncertainApprovedActionExecutor(),
        checkpointer=InMemorySaver(),
    )
    context = _context()
    config = {"configurable": {"thread_id": f"gate-uncertain-{uuid4()}"}}
    await graph.ainvoke({"proposal": _proposal()}, config=config, context=context)

    resumed = await graph.ainvoke(
        Command(
            resume={
                "approval_request_id": str(creator.approval_request_id),
                "status": "approved",
            }
        ),
        config=config,
        context=context,
    )

    assert resumed["execution_status"] is WriteExecutionStatus.UNCERTAIN
    assert resumed["error_code"] == "MCP_TOOL_INVOCATION_FAILED"


def test_idempotency_key_is_deterministic_and_versioned() -> None:
    run_id = uuid4()

    first = build_approval_idempotency_key(
        agent_run_id=run_id,
        action=ApprovalAction.CREATE_EVENT,
        version=1,
    )
    repeated = build_approval_idempotency_key(
        agent_run_id=run_id,
        action=ApprovalAction.CREATE_EVENT,
        version=1,
    )
    next_version = build_approval_idempotency_key(
        agent_run_id=run_id,
        action=ApprovalAction.CREATE_EVENT,
        version=2,
    )

    assert first == repeated
    assert first != next_version
