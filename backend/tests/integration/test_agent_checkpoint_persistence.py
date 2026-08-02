"""真实 PostgreSQL Checkpointer 跨连接暂停和恢复测试。"""

import os
from uuid import UUID, uuid4

import pytest
from app.agent.approval_graph import build_approval_gate_graph
from app.agent.approval_schemas import (
    ApprovalGateStatus,
    SendEmailArguments,
    WriteActionExecution,
    WriteActionProposal,
)
from app.agent.checkpoint import open_postgres_checkpointer
from app.agent.context import AgentRuntimeContext
from app.agent.nodes.approval import PreparedApproval
from app.agent.nodes.execute_write import ExecutedApprovedAction
from app.core.config import Settings
from app.models.approval import ApprovalAction, ApprovalStatus
from langgraph.types import Command

pytestmark = pytest.mark.integration


class FakeApprovalCreator:
    def __init__(self, approval_request_id: UUID) -> None:
        self.approval_request_id = approval_request_id
        self.call_count = 0

    async def create(self, **_: object) -> PreparedApproval:
        self.call_count += 1
        return PreparedApproval(
            approval_request_id=self.approval_request_id,
            status=ApprovalStatus.PENDING,
        )


class FakeApprovedActionExecutor:
    def __init__(self) -> None:
        self.call_count = 0

    async def execute(self, **_: object) -> ExecutedApprovedAction:
        self.call_count += 1
        return ExecutedApprovedAction(
            execution=WriteActionExecution(
                action=ApprovalAction.SEND_EMAIL,
                tool_name="send_email",
                idempotency_key="checkpoint-write-001",
                arguments={"draft_message_id": str(uuid4())},
                result={"success": True},
            )
        )


def _settings(database_url: str) -> Settings:
    return Settings(
        environment="test",
        database_url=database_url,
        database_pool_size=2,
        redis_url="redis://localhost:6379/15",
        jwt_secret_key="checkpoint-test-secret-key-at-least-32-characters",
    )


async def test_checkpoint_survives_connection_restart_and_resumes() -> None:
    database_url = os.getenv("TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("未配置 TEST_DATABASE_URL，跳过 PostgreSQL Checkpointer 测试")

    settings = _settings(database_url)
    graph_thread_id = f"checkpoint-{uuid4()}"
    config = {"configurable": {"thread_id": graph_thread_id}}
    approval_request_id = uuid4()
    context = AgentRuntimeContext(
        user_id=uuid4(),
        agent_run_id=uuid4(),
        request_id="checkpoint-integration",
        user_timezone="Asia/Shanghai",
    )
    proposal = WriteActionProposal(
        action=ApprovalAction.SEND_EMAIL,
        arguments=SendEmailArguments(draft_message_id=uuid4()),
        summary="发送已确认的项目回复",
    )
    first_creator = FakeApprovalCreator(approval_request_id)
    first_executor = FakeApprovedActionExecutor()

    async with open_postgres_checkpointer(settings, setup=True) as first_checkpointer:
        first_graph = build_approval_gate_graph(
            creator=first_creator,
            executor=first_executor,
            checkpointer=first_checkpointer,
        )
        paused = await first_graph.ainvoke(
            {"proposal": proposal},
            config=config,
            context=context,
        )
        assert paused["__interrupt__"]
        assert first_creator.call_count == 1

    second_creator = FakeApprovalCreator(approval_request_id)
    second_executor = FakeApprovedActionExecutor()
    async with open_postgres_checkpointer(settings) as second_checkpointer:
        second_graph = build_approval_gate_graph(
            creator=second_creator,
            executor=second_executor,
            checkpointer=second_checkpointer,
        )
        snapshot = await second_graph.aget_state(config)
        assert snapshot.values["proposal"] == proposal
        assert snapshot.values["gate_status"] == ApprovalGateStatus.WAITING_APPROVAL

        resumed = await second_graph.ainvoke(
            Command(
                resume={
                    "approval_request_id": str(approval_request_id),
                    "status": "approved",
                }
            ),
            config=config,
            context=context,
        )
        assert resumed["gate_status"] is ApprovalGateStatus.RESUMED
        assert second_creator.call_count == 0
        assert second_executor.call_count == 1
        await second_checkpointer.adelete_thread(graph_thread_id)
