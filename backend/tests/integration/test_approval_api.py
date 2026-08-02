"""审批 API、权限、幂等决定与真实 PostgreSQL Graph 恢复测试。"""

import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from app.agent.approval_graph import build_approval_gate_graph
from app.agent.approval_schemas import (
    SendEmailArguments,
    WriteActionExecution,
    WriteActionProposal,
)
from app.agent.checkpoint import open_postgres_checkpointer
from app.agent.context import AgentRuntimeContext
from app.agent.exceptions import ApprovedActionExecutionError
from app.agent.nodes.approval import ServiceApprovalRequestCreator
from app.agent.nodes.execute_write import ExecutedApprovedAction
from app.core.config import Settings
from app.main import app
from app.models.agent_run import AgentRun, AgentRunStatus
from app.models.approval import ApprovalAction, ApprovalStatus
from app.models.user import User, UserRole
from app.repositories.audit import AuditRepository
from app.schemas.agent_run import AgentRunCreate
from app.schemas.email import EmailMessageImport
from app.schemas.user import UserCreate
from app.security.jwt import create_access_token
from app.services.agent_run import AgentRunService
from app.services.approval import ApprovalService
from app.services.email import EmailService
from app.services.user import UserService
from httpx import AsyncClient
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = pytest.mark.integration


class FakeApprovedActionExecutor:
    """审批 API 测试只验证编排，真实 MCP 写入由独立集成测试负责。"""

    async def execute(self, **_: object) -> ExecutedApprovedAction:
        return ExecutedApprovedAction(
            execution=WriteActionExecution(
                action=ApprovalAction.SEND_EMAIL,
                tool_name="send_email",
                idempotency_key="approval-api-write-001",
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


def _checkpoint_settings(database_url: str) -> Settings:
    return Settings(
        environment="test",
        database_url=database_url,
        database_pool_size=2,
        redis_url="redis://localhost:6379/15",
        jwt_secret_key="approval-api-secret-key-at-least-32-characters",
    )


def _auth_headers(user_id: UUID) -> dict[str, str]:
    return {"Authorization": f"Bearer {create_access_token(user_id)}"}


async def _create_user(
    session: AsyncSession,
    *,
    email: str,
) -> User:
    return await UserService(session).create_user(
        UserCreate(
            email=email,
            password="approval-api-password",
            full_name="审批 API 测试用户",
            timezone="Asia/Shanghai",
        )
    )


async def _create_paused_send_email_approval(
    session: AsyncSession,
    checkpointer: AsyncPostgresSaver,
    *,
    email: str,
    suffix: str,
) -> tuple[User, AgentRun, UUID]:
    user = await _create_user(session, email=email)
    imported = await EmailService(session).import_inbound_email(
        user_id=user.id,
        data=EmailMessageImport(
            provider="local",
            thread_external_id=f"approval-api-thread-{suffix}",
            message_external_id=f"approval-api-message-{suffix}",
            subject="请确认项目安排",
            sender="manager@example.com",
            recipients=[email],
            body_text="请回复确认。",
            sent_at=datetime(2026, 7, 24, 2, 0, tzinfo=UTC),
        ),
    )
    run = await AgentRunService(session).create_run(
        user_id=user.id,
        data=AgentRunCreate(
            email_thread_id=imported.thread.id,
            graph_thread_id=f"approval-api-graph-{suffix}-{uuid4()}",
        ),
    )
    run = await AgentRunService(session).transition_status(
        user_id=user.id,
        run_id=run.id,
        target_status=AgentRunStatus.RUNNING,
        current_node="prepare_write_action",
    )

    @asynccontextmanager
    async def session_factory() -> AsyncIterator[AsyncSession]:
        yield session

    graph = build_approval_gate_graph(
        creator=ServiceApprovalRequestCreator(session_factory),
        executor=FakeApprovedActionExecutor(),
        checkpointer=checkpointer,
    )
    paused = await graph.ainvoke(
        {
            "proposal": WriteActionProposal(
                action=ApprovalAction.SEND_EMAIL,
                arguments=SendEmailArguments(draft_message_id=uuid4()),
                summary="向项目经理发送确认邮件",
            )
        },
        config={"configurable": {"thread_id": run.graph_thread_id}},
        context=AgentRuntimeContext(
            user_id=user.id,
            agent_run_id=run.id,
            request_id=f"pause-{suffix}",
            user_timezone=user.timezone,
        ),
    )
    approval_id = UUID(paused["__interrupt__"][0].value["approval_request_id"])
    return user, run, approval_id


@asynccontextmanager
async def _app_checkpointer(
    database_url: str,
) -> AsyncIterator[AsyncPostgresSaver]:
    previous = getattr(app.state, "agent_checkpointer", None)
    previous_executor = getattr(app.state, "approved_action_executor", None)
    async with open_postgres_checkpointer(
        _checkpoint_settings(database_url),
        setup=True,
    ) as checkpointer:
        app.state.agent_checkpointer = checkpointer
        app.state.approved_action_executor = FakeApprovedActionExecutor()
        try:
            yield checkpointer
        finally:
            if previous is None:
                del app.state.agent_checkpointer
            else:
                app.state.agent_checkpointer = previous
            if previous_executor is None:
                del app.state.approved_action_executor
            else:
                app.state.approved_action_executor = previous_executor


async def test_owner_approves_admin_reads_and_same_request_retries(
    integration_session: AsyncSession,
    integration_client: AsyncClient,
) -> None:
    database_url = os.environ["TEST_DATABASE_URL"]
    async with _app_checkpointer(database_url) as checkpointer:
        owner, run, approval_id = await _create_paused_send_email_approval(
            integration_session,
            checkpointer,
            email="approval-api-owner@example.com",
            suffix="owner",
        )
        stranger = await _create_user(
            integration_session,
            email="approval-api-stranger@example.com",
        )
        admin = await _create_user(
            integration_session,
            email="approval-api-admin@example.com",
        )
        admin.role = UserRole.ADMIN
        await integration_session.commit()
        owner_id = owner.id
        stranger_id = stranger.id
        admin_id = admin.id
        run_id = run.id
        graph_thread_id = run.graph_thread_id

        unauthenticated = await integration_client.get("/api/v1/approvals")
        owner_list = await integration_client.get(
            "/api/v1/approvals",
            headers=_auth_headers(owner_id),
        )
        stranger_detail = await integration_client.get(
            f"/api/v1/approvals/{approval_id}",
            headers=_auth_headers(stranger_id),
        )
        admin_list = await integration_client.get(
            "/api/v1/approvals",
            headers=_auth_headers(admin_id),
        )
        admin_detail = await integration_client.get(
            f"/api/v1/approvals/{approval_id}",
            headers=_auth_headers(admin_id),
        )
        admin_decision = await integration_client.post(
            f"/api/v1/approvals/{approval_id}/approve",
            headers=_auth_headers(admin_id),
        )

        assert unauthenticated.status_code == 401
        assert owner_list.status_code == 200
        assert owner_list.json()["data"]["total"] == 1
        assert stranger_detail.status_code == 404
        assert admin_list.json()["data"]["total"] == 1
        assert admin_detail.json()["data"]["user_id"] == str(owner_id)
        assert admin_decision.status_code == 404

        first = await integration_client.post(
            f"/api/v1/approvals/{approval_id}/approve",
            headers={**_auth_headers(owner_id), "X-Request-ID": "approval-approve-001"},
        )
        repeated = await integration_client.post(
            f"/api/v1/approvals/{approval_id}/approve",
            headers=_auth_headers(owner_id),
        )

        assert first.status_code == 200
        assert first.json()["data"]["approval"]["status"] == "approved"
        assert first.json()["data"]["graph_resumed"] is True
        assert first.json()["data"]["already_resumed"] is False
        assert first.json()["request_id"] == "approval-approve-001"
        assert repeated.status_code == 200
        assert repeated.json()["data"]["graph_resumed"] is False
        assert repeated.json()["data"]["already_resumed"] is True

        run_after = await AgentRunService(integration_session).get_run(
            user_id=owner_id,
            run_id=run_id,
        )
        assert run_after.status is AgentRunStatus.RUNNING
        assert run_after.current_node == "write_action_executed"
        actions = [
            event.action
            for event in await AuditRepository(integration_session).list_events(user_id=owner_id)
        ]
        assert actions.count("approval.approved") == 1
        await checkpointer.adelete_thread(graph_thread_id)


async def test_modified_arguments_are_validated_before_resume(
    integration_session: AsyncSession,
    integration_client: AsyncClient,
) -> None:
    database_url = os.environ["TEST_DATABASE_URL"]
    async with _app_checkpointer(database_url) as checkpointer:
        user, run, approval_id = await _create_paused_send_email_approval(
            integration_session,
            checkpointer,
            email="approval-api-modify@example.com",
            suffix="modify",
        )
        user_id = user.id
        graph_thread_id = run.graph_thread_id
        headers = _auth_headers(user_id)

        invalid = await integration_client.post(
            f"/api/v1/approvals/{approval_id}/approve-with-modifications",
            json={"modified_arguments": {"event_id": str(uuid4())}},
            headers=headers,
        )
        assert invalid.status_code == 422
        assert invalid.json()["error"]["code"] == "APPROVAL_ARGUMENTS_INVALID"
        pending = await ApprovalService(integration_session).get_request(
            user_id=user_id,
            approval_id=approval_id,
        )
        assert pending.status is ApprovalStatus.PENDING

        modified_draft_id = uuid4()
        accepted = await integration_client.post(
            f"/api/v1/approvals/{approval_id}/approve-with-modifications",
            json={"modified_arguments": {"draft_message_id": str(modified_draft_id)}},
            headers=headers,
        )

        assert accepted.status_code == 200
        assert accepted.json()["data"]["approval"]["modified_arguments"] == {
            "draft_message_id": str(modified_draft_id)
        }
        await checkpointer.adelete_thread(graph_thread_id)


async def test_reject_and_regeneration_endpoints_resume_their_own_threads(
    integration_session: AsyncSession,
    integration_client: AsyncClient,
) -> None:
    database_url = os.environ["TEST_DATABASE_URL"]
    async with _app_checkpointer(database_url) as checkpointer:
        reject_user, reject_run, reject_id = await _create_paused_send_email_approval(
            integration_session,
            checkpointer,
            email="approval-api-reject@example.com",
            suffix="reject",
        )
        feedback_user, feedback_run, feedback_id = await _create_paused_send_email_approval(
            integration_session,
            checkpointer,
            email="approval-api-feedback@example.com",
            suffix="feedback",
        )
        reject_user_id = reject_user.id
        reject_run_id = reject_run.id
        reject_thread_id = reject_run.graph_thread_id
        feedback_user_id = feedback_user.id
        feedback_run_id = feedback_run.id
        feedback_thread_id = feedback_run.graph_thread_id

        rejected = await integration_client.post(
            f"/api/v1/approvals/{reject_id}/reject",
            json={"feedback": "这封邮件不应发送"},
            headers=_auth_headers(reject_user_id),
        )
        regeneration = await integration_client.post(
            f"/api/v1/approvals/{feedback_id}/request-regeneration",
            json={"feedback": "语气更简洁，并补充会议地点"},
            headers=_auth_headers(feedback_user_id),
        )

        assert rejected.status_code == 200
        assert rejected.json()["data"]["approval"]["status"] == "rejected"
        assert regeneration.status_code == 200
        assert regeneration.json()["data"]["approval"]["status"] == "feedback_requested"
        assert regeneration.json()["data"]["approval"]["feedback"] == ("语气更简洁，并补充会议地点")

        rejected_run = await AgentRunService(integration_session).get_run(
            user_id=reject_user_id,
            run_id=reject_run_id,
        )
        feedback_run_after = await AgentRunService(integration_session).get_run(
            user_id=feedback_user_id,
            run_id=feedback_run_id,
        )
        assert rejected_run.status is AgentRunStatus.CANCELLED
        assert feedback_run_after.status is AgentRunStatus.RUNNING
        assert feedback_run_after.current_node == "approval_feedback_received"
        await checkpointer.adelete_thread(reject_thread_id)
        await checkpointer.adelete_thread(feedback_thread_id)


async def test_saved_decision_can_retry_when_checkpoint_is_temporarily_missing(
    integration_session: AsyncSession,
    integration_client: AsyncClient,
) -> None:
    """恢复失败不能撤销已审计决定，相同决定也不能被误判为重复决定。"""

    database_url = os.environ["TEST_DATABASE_URL"]
    async with _app_checkpointer(database_url) as checkpointer:
        user, run, approval_id = await _create_paused_send_email_approval(
            integration_session,
            checkpointer,
            email="approval-api-missing-checkpoint@example.com",
            suffix="missing-checkpoint",
        )
        user_id = user.id
        run_id = run.id
        graph_thread_id = run.graph_thread_id
        await checkpointer.adelete_thread(graph_thread_id)

        first = await integration_client.post(
            f"/api/v1/approvals/{approval_id}/approve",
            headers=_auth_headers(user_id),
        )
        repeated = await integration_client.post(
            f"/api/v1/approvals/{approval_id}/approve",
            headers=_auth_headers(user_id),
        )

        assert first.status_code == 409
        assert first.json()["error"]["code"] == "AGENT_CHECKPOINT_NOT_FOUND"
        assert repeated.status_code == 409
        assert repeated.json()["error"]["code"] == "AGENT_CHECKPOINT_NOT_FOUND"
        approval = await ApprovalService(integration_session).get_request(
            user_id=user_id,
            approval_id=approval_id,
        )
        run_after = await AgentRunService(integration_session).get_run(
            user_id=user_id,
            run_id=run_id,
        )
        assert approval.status is ApprovalStatus.APPROVED
        assert run_after.status is AgentRunStatus.WAITING_APPROVAL
        actions = [
            event.action
            for event in await AuditRepository(integration_session).list_events(user_id=user_id)
        ]
        assert actions.count("approval.approved") == 1


async def test_uncertain_write_result_marks_agent_run_failed(
    integration_session: AsyncSession,
    integration_client: AsyncClient,
) -> None:
    """超时结果未知时不能假装成功，也不能自动再次调用写工具。"""

    database_url = os.environ["TEST_DATABASE_URL"]
    async with _app_checkpointer(database_url) as checkpointer:
        user, run, approval_id = await _create_paused_send_email_approval(
            integration_session,
            checkpointer,
            email="approval-api-uncertain@example.com",
            suffix="uncertain",
        )
        user_id = user.id
        run_id = run.id
        graph_thread_id = run.graph_thread_id
        app.state.approved_action_executor = UncertainApprovedActionExecutor()

        response = await integration_client.post(
            f"/api/v1/approvals/{approval_id}/approve",
            headers=_auth_headers(user_id),
        )

        assert response.status_code == 200
        run_after = await AgentRunService(integration_session).get_run(
            user_id=user_id,
            run_id=run_id,
        )
        assert run_after.status is AgentRunStatus.FAILED
        assert run_after.current_node == "write_action_failed"
        assert run_after.error_code == "MCP_TOOL_INVOCATION_FAILED"
        await checkpointer.adelete_thread(graph_thread_id)
