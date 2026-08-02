"""完整邮件 Agent 启动、暂停、查询和审批恢复 API 集成测试。"""

import json
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from app.agent.approval_schemas import ApprovalGateStatus, ApprovalResumeSignal
from app.agent.checkpoint import open_postgres_checkpointer
from app.agent.nodes.approval import ServiceApprovalRequestCreator
from app.agent.nodes.memory_feedback import ServiceMemoryFeedbackUpdater
from app.agent.schemas import ModelUsage
from app.agent.workflow import build_mail_processing_graph
from app.core.config import Settings
from app.integrations.llm.client import StructuredLlmResult
from app.main import app
from app.memory.store import memory_store_namespace, open_postgres_store
from app.models.agent_run import AgentRunStatus
from app.models.memory import MemoryProfileVersion, MemoryType
from app.schemas.email import EmailMessageImport
from app.schemas.memory import MemoryProfileCreate
from app.schemas.memory_feedback import (
    EmailStyleMemoryPatch,
    EmailStyleMemoryUpdateProposal,
    FeedbackMemoryDecision,
)
from app.schemas.user import UserCreate
from app.security.jwt import create_access_token
from app.services.agent_run import AgentRunService
from app.services.email import EmailService
from app.services.memory import MemoryService
from app.services.user import UserService
from httpx import AsyncClient
from langchain_core.messages import BaseMessage
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from tests.agent.test_mail_processing_workflow import (
    FakeDraftClient,
    FakeExecutor,
    FakeLlm,
    FakeReader,
)

pytestmark = pytest.mark.integration


class FakeLongTermFeedbackLlm(FakeLlm):
    """只在记忆反馈步骤额外返回一个结构化邮件风格 Patch。"""

    async def ainvoke_structured[SchemaT: BaseModel](
        self,
        *,
        operation: str,
        messages: list[BaseMessage],
        schema: type[SchemaT],
    ) -> StructuredLlmResult[SchemaT]:
        if operation != "analyze_memory_feedback":
            return await super().ainvoke_structured(
                operation=operation,
                messages=messages,
                schema=schema,
            )
        self.operations.append(operation)
        parsed = FeedbackMemoryDecision(
            should_update=True,
            proposal=EmailStyleMemoryUpdateProposal(
                memory_type=MemoryType.EMAIL_STYLE,
                patch=EmailStyleMemoryPatch(tone="简洁专业"),
                evidence="以后邮件都使用简洁专业语气",
                reason="用户明确要求长期使用该语气",
            ),
            reason="属于长期邮件风格偏好",
            confidence=0.98,
        )
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


def _settings(database_url: str) -> Settings:
    return Settings(
        environment="test",
        database_url=database_url,
        database_pool_size=2,
        redis_url="redis://localhost:6379/15",
        jwt_secret_key="mail-processing-api-secret-at-least-32-characters",
    )


def _auth_headers(user_id: UUID) -> dict[str, str]:
    return {"Authorization": f"Bearer {create_access_token(user_id)}"}


async def _run_detail(
    client: AsyncClient,
    *,
    user_id: UUID,
    run_id: UUID,
) -> dict[str, object]:
    response = await client.get(
        f"/api/v1/agent-runs/{run_id}",
        headers=_auth_headers(user_id),
    )
    assert response.status_code == 200, response.text
    return response.json()["data"]


async def _pending_approval_id(
    client: AsyncClient,
    *,
    user_id: UUID,
    run_id: UUID,
) -> UUID:
    response = await client.get(
        "/api/v1/approvals",
        params={"status": "pending", "limit": 100},
        headers=_auth_headers(user_id),
    )
    assert response.status_code == 200, response.text
    item = next(
        approval
        for approval in response.json()["data"]["items"]
        if approval["agent_run_id"] == str(run_id)
    )
    return UUID(item["id"])


@asynccontextmanager
async def _configured_app(
    *,
    database_url: str,
    session: AsyncSession,
    llm: FakeLlm | None = None,
) -> AsyncIterator[AsyncPostgresSaver]:
    previous_checkpointer = getattr(app.state, "agent_checkpointer", None)
    previous_executor = getattr(app.state, "approved_action_executor", None)
    previous_factory = getattr(app.state, "mail_processing_graph_factory", None)
    previous_store = getattr(app.state, "agent_store", None)
    previous_session_factory = getattr(
        app.state,
        "agent_background_session_factory",
        None,
    )
    settings = _settings(database_url)
    async with (
        open_postgres_checkpointer(settings, setup=True) as checkpointer,
        open_postgres_store(settings, setup=True) as store,
    ):
        executor = FakeExecutor()
        graph_llm = llm or FakeLlm()
        draft_client = FakeDraftClient()

        @asynccontextmanager
        async def session_factory() -> AsyncIterator[AsyncSession]:
            yield session

        def graph_factory():
            return build_mail_processing_graph(
                reader=FakeReader(),
                llm_client=graph_llm,
                read_tool_client_factory=lambda _: draft_client,
                safe_tool_client_factory=lambda _: draft_client,
                approval_creator=ServiceApprovalRequestCreator(session_factory),
                memory_feedback_updater=ServiceMemoryFeedbackUpdater(session_factory),
                executor=executor,
                checkpointer=checkpointer,
                store=store,
            )

        app.state.agent_checkpointer = checkpointer
        app.state.approved_action_executor = executor
        app.state.mail_processing_graph_factory = graph_factory
        app.state.agent_store = store
        app.state.agent_background_session_factory = session_factory
        try:
            yield checkpointer
        finally:
            for name, previous in (
                ("agent_checkpointer", previous_checkpointer),
                ("approved_action_executor", previous_executor),
                ("mail_processing_graph_factory", previous_factory),
                ("agent_store", previous_store),
                ("agent_background_session_factory", previous_session_factory),
            ):
                if previous is None:
                    delattr(app.state, name)
                else:
                    setattr(app.state, name, previous)


async def test_start_query_permission_and_approve_complete(
    integration_session: AsyncSession,
    integration_client: AsyncClient,
) -> None:
    database_url = os.environ["TEST_DATABASE_URL"]
    owner = await UserService(integration_session).create_user(
        UserCreate(
            email="mail-flow-owner@example.com",
            password="mail-flow-password",
            full_name="完整流程用户",
            timezone="Asia/Shanghai",
        )
    )
    stranger = await UserService(integration_session).create_user(
        UserCreate(
            email="mail-flow-stranger@example.com",
            password="mail-flow-password",
            full_name="其他用户",
            timezone="Asia/Shanghai",
        )
    )
    imported = await EmailService(integration_session).import_inbound_email(
        user_id=owner.id,
        data=EmailMessageImport(
            provider="local",
            thread_external_id=f"mail-flow-thread-{uuid4()}",
            message_external_id=f"mail-flow-message-{uuid4()}",
            subject="请确认项目进度",
            sender="manager@example.com",
            recipients=[owner.email],
            body_text="请回复确认今天能否完成。",
            sent_at=datetime(2026, 7, 24, 2, 0, tzinfo=UTC),
        ),
    )
    await MemoryService(integration_session).create_profile(
        user_id=owner.id,
        actor_user_id=owner.id,
        data=MemoryProfileCreate(
            memory_type=MemoryType.EMAIL_STYLE,
            value={
                "tone": "简洁专业",
                "signature": "完整流程用户｜研发部",
            },
        ),
    )
    owner_id = owner.id
    stranger_id = stranger.id
    thread_id = imported.thread.id

    async with _configured_app(
        database_url=database_url,
        session=integration_session,
    ) as checkpointer:
        started = await integration_client.post(
            f"/api/v1/emails/{thread_id}/process",
            headers={**_auth_headers(owner_id), "X-Request-ID": "mail-flow-start-001"},
        )

        assert started.status_code == 202, started.text
        data = started.json()["data"]
        run_id = UUID(data["run"]["id"])
        graph_thread_id = data["run"]["graph_thread_id"]
        assert data["run"]["workflow_name"] == "mail_processing_v1"
        assert data["run"]["status"] == "pending"
        assert data["approval_request_id"] is None

        owner_detail = await integration_client.get(
            f"/api/v1/agent-runs/{run_id}",
            headers=_auth_headers(owner_id),
        )
        owner_run = owner_detail.json()["data"]
        approval_id = await _pending_approval_id(
            integration_client,
            user_id=owner_id,
            run_id=run_id,
        )
        assert owner_run["status"] == "waiting_approval"
        assert owner_run["total_tokens"] == 60
        assert owner_run["result"]["draft"]["body_text"]
        assert owner_run["result"]["memory_context"]["email_style"]["tone"] == "简洁专业"
        assert owner_run["result"]["memory_context"]["references"][0]["version"] == 1
        assert "email_body" not in owner_run["result"]

        stranger_detail = await integration_client.get(
            f"/api/v1/agent-runs/{run_id}",
            headers=_auth_headers(stranger_id),
        )
        assert owner_detail.status_code == 200
        assert stranger_detail.status_code == 404

        history = await integration_client.get(
            f"/api/v1/agent-runs/{run_id}/events/history",
            headers=_auth_headers(owner_id),
        )
        stranger_history = await integration_client.get(
            f"/api/v1/agent-runs/{run_id}/events/history",
            headers=_auth_headers(stranger_id),
        )
        assert history.status_code == 200, history.text
        assert stranger_history.status_code == 404
        events = history.json()["data"]["items"]
        sequences = [event["sequence"] for event in events]
        assert sequences == list(range(1, len(sequences) + 1))
        assert events[0]["event_type"] == "run_created"
        assert "node_completed" in {event["event_type"] for event in events}
        assert events[-1]["event_type"] == "approval_required"
        assert "请回复确认今天能否完成" not in json.dumps(events, ensure_ascii=False)

        resumed_stream = await integration_client.get(
            f"/api/v1/agent-runs/{run_id}/events",
            headers={
                **_auth_headers(owner_id),
                "Last-Event-ID": str(sequences[-2]),
            },
        )
        assert resumed_stream.status_code == 200
        assert f"id: {sequences[-1]}" in resumed_stream.text
        assert "event: stream_closed" in resumed_stream.text

        approved = await integration_client.post(
            f"/api/v1/approvals/{approval_id}/approve",
            headers=_auth_headers(owner_id),
        )

        assert approved.status_code == 200
        assert approved.json()["data"]["graph_resumed"] is True
        run_after = await AgentRunService(integration_session).get_run(
            user_id=owner_id,
            run_id=run_id,
        )
        assert run_after.status is AgentRunStatus.COMPLETED
        assert run_after.current_node == "finalize_success"
        assert run_after.result["execution_status"] == "succeeded"
        await checkpointer.adelete_thread(graph_thread_id)


async def test_explicit_feedback_updates_memory_before_repausing(
    integration_session: AsyncSession,
    integration_client: AsyncClient,
) -> None:
    database_url = os.environ["TEST_DATABASE_URL"]
    user = await UserService(integration_session).create_user(
        UserCreate(
            email=f"mail-flow-memory-{uuid4()}@example.com",
            password="mail-flow-password",
            full_name="长期反馈用户",
            timezone="Asia/Shanghai",
        )
    )
    imported = await EmailService(integration_session).import_inbound_email(
        user_id=user.id,
        data=EmailMessageImport(
            provider="local",
            thread_external_id=f"mail-memory-thread-{uuid4()}",
            message_external_id=f"mail-memory-message-{uuid4()}",
            subject="请确认项目进度",
            sender="manager@example.com",
            recipients=[user.email],
            body_text="请回复确认今天能否完成。",
            sent_at=datetime(2026, 7, 24, 2, 0, tzinfo=UTC),
        ),
    )
    second_imported = await EmailService(integration_session).import_inbound_email(
        user_id=user.id,
        data=EmailMessageImport(
            provider="local",
            thread_external_id=f"mail-memory-second-thread-{uuid4()}",
            message_external_id=f"mail-memory-second-message-{uuid4()}",
            subject="第二封需要回复的邮件",
            sender="manager@example.com",
            recipients=[user.email],
            body_text="请再次确认项目进度。",
            sent_at=datetime(2026, 7, 24, 3, 0, tzinfo=UTC),
        ),
    )
    third_imported = await EmailService(integration_session).import_inbound_email(
        user_id=user.id,
        data=EmailMessageImport(
            provider="local",
            thread_external_id=f"mail-memory-third-thread-{uuid4()}",
            message_external_id=f"mail-memory-third-message-{uuid4()}",
            subject="删除偏好后的邮件",
            sender="manager@example.com",
            recipients=[user.email],
            body_text="请确认删除偏好后是否仍可处理。",
            sent_at=datetime(2026, 7, 24, 4, 0, tzinfo=UTC),
        ),
    )
    llm = FakeLongTermFeedbackLlm()
    async with _configured_app(
        database_url=database_url,
        session=integration_session,
        llm=llm,
    ) as checkpointer:
        started = await integration_client.post(
            f"/api/v1/emails/{imported.thread.id}/process",
            headers=_auth_headers(user.id),
        )
        run_id = UUID(started.json()["data"]["run"]["id"])
        graph_thread_id = started.json()["data"]["run"]["graph_thread_id"]
        first_approval_id = await _pending_approval_id(
            integration_client,
            user_id=user.id,
            run_id=run_id,
        )

        feedback = await integration_client.post(
            f"/api/v1/approvals/{first_approval_id}/request-regeneration",
            json={"feedback": "以后邮件都使用简洁专业语气，请记住"},
            headers=_auth_headers(user.id),
        )
        repeated = await integration_client.post(
            f"/api/v1/approvals/{first_approval_id}/request-regeneration",
            json={"feedback": "以后邮件都使用简洁专业语气，请记住"},
            headers=_auth_headers(user.id),
        )

        assert feedback.status_code == 200, feedback.text
        assert repeated.status_code == 200, repeated.text
        assert repeated.json()["data"]["already_resumed"] is True
        assert llm.operations.count("analyze_memory_feedback") == 1

        run = await AgentRunService(integration_session).get_run(
            user_id=user.id,
            run_id=run_id,
        )
        assert run.status is AgentRunStatus.WAITING_APPROVAL
        assert run.result["memory_update"]["applied"] is True
        assert run.result["memory_update"]["version"] == 1
        assert run.result["memory_feedback_decision"]["should_update"] is True
        assert run.total_tokens == 90
        memory_id = UUID(run.result["memory_update"]["memory_id"])

        history = await integration_client.get(
            f"/api/v1/memories/{memory_id}/versions",
            headers=_auth_headers(user.id),
        )
        assert history.status_code == 200, history.text
        history_item = history.json()["data"]["items"][0]
        assert history_item["version"] == 1
        assert history_item["source_type"] == "approval_feedback"
        assert history_item["source_reference_type"] == "approval_request"
        assert history_item["source_reference_id"] == str(first_approval_id)

        version_count = await integration_session.scalar(
            select(func.count())
            .select_from(MemoryProfileVersion)
            .where(
                MemoryProfileVersion.user_id == user.id,
                MemoryProfileVersion.source_reference_id == first_approval_id,
            )
        )
        assert version_count == 1

        second_started = await integration_client.post(
            f"/api/v1/emails/{second_imported.thread.id}/process",
            headers=_auth_headers(user.id),
        )
        assert second_started.status_code == 202, second_started.text
        second_run = await _run_detail(
            integration_client,
            user_id=user.id,
            run_id=UUID(second_started.json()["data"]["run"]["id"]),
        )
        second_graph_thread_id = second_run["graph_thread_id"]
        assert second_run["result"]["memory_context"]["email_style"]["tone"] == "简洁专业"
        assert second_run["result"]["memory_context"]["references"][0]["version"] == 1

        deleted = await integration_client.delete(
            f"/api/v1/memories/{memory_id}",
            headers=_auth_headers(user.id),
        )
        assert deleted.status_code == 200, deleted.text

        third_started = await integration_client.post(
            f"/api/v1/emails/{third_imported.thread.id}/process",
            headers=_auth_headers(user.id),
        )
        assert third_started.status_code == 202, third_started.text
        third_run = await _run_detail(
            integration_client,
            user_id=user.id,
            run_id=UUID(third_started.json()["data"]["run"]["id"]),
        )
        third_graph_thread_id = third_run["graph_thread_id"]
        assert third_run["result"]["memory_context"]["email_style"] is None
        assert third_run["result"]["memory_context"]["references"] == []
        stale_store_item = await app.state.agent_store.aget(
            memory_store_namespace(
                user_id=user.id,
                memory_type=MemoryType.EMAIL_STYLE,
            ),
            "default",
        )
        assert stale_store_item is None

        await checkpointer.adelete_thread(graph_thread_id)
        await checkpointer.adelete_thread(second_graph_thread_id)
        await checkpointer.adelete_thread(third_graph_thread_id)


async def test_feedback_repauses_with_next_approval_and_is_idempotent(
    integration_session: AsyncSession,
    integration_client: AsyncClient,
) -> None:
    database_url = os.environ["TEST_DATABASE_URL"]
    user = await UserService(integration_session).create_user(
        UserCreate(
            email="mail-flow-feedback@example.com",
            password="mail-flow-password",
            full_name="反馈流程用户",
            timezone="Asia/Shanghai",
        )
    )
    imported = await EmailService(integration_session).import_inbound_email(
        user_id=user.id,
        data=EmailMessageImport(
            provider="local",
            thread_external_id=f"mail-feedback-thread-{uuid4()}",
            message_external_id=f"mail-feedback-message-{uuid4()}",
            subject="请确认项目进度",
            sender="manager@example.com",
            recipients=[user.email],
            body_text="请回复确认今天能否完成。",
            sent_at=datetime(2026, 7, 24, 2, 0, tzinfo=UTC),
        ),
    )
    user_id = user.id
    async with _configured_app(
        database_url=database_url,
        session=integration_session,
    ) as checkpointer:
        started = await integration_client.post(
            f"/api/v1/emails/{imported.thread.id}/process",
            headers=_auth_headers(user_id),
        )
        run_id = UUID(started.json()["data"]["run"]["id"])
        graph_thread_id = started.json()["data"]["run"]["graph_thread_id"]
        first_approval_id = await _pending_approval_id(
            integration_client,
            user_id=user_id,
            run_id=run_id,
        )

        first_feedback = await integration_client.post(
            f"/api/v1/approvals/{first_approval_id}/request-regeneration",
            json={"feedback": "语气再简洁一点"},
            headers=_auth_headers(user_id),
        )
        snapshot = await app.state.mail_processing_graph_factory().aget_state(
            {"configurable": {"thread_id": graph_thread_id}}
        )
        signal = ApprovalResumeSignal.model_validate(snapshot.values.get("resume_signal"))
        assert signal.approval_request_id == first_approval_id
        assert signal.status.value == "feedback_requested"
        assert (
            ApprovalGateStatus(snapshot.values["gate_status"])
            is ApprovalGateStatus.WAITING_APPROVAL
        )
        repeated_feedback = await integration_client.post(
            f"/api/v1/approvals/{first_approval_id}/request-regeneration",
            json={"feedback": "语气再简洁一点"},
            headers=_auth_headers(user_id),
        )

        assert first_feedback.status_code == 200
        first_data = first_feedback.json()["data"]
        next_approval_id = UUID(first_data["next_approval_request_id"])
        assert next_approval_id != first_approval_id
        assert first_data["graph_resumed"] is True
        assert repeated_feedback.status_code == 200, repeated_feedback.text
        repeated_data = repeated_feedback.json()["data"]
        assert repeated_data["already_resumed"] is True
        assert repeated_data["next_approval_request_id"] == str(next_approval_id)

        run = await AgentRunService(integration_session).get_run(
            user_id=user_id,
            run_id=run_id,
        )
        assert run.status is AgentRunStatus.WAITING_APPROVAL
        assert run.current_node == "wait_for_approval"
        assert run.total_tokens == 75
        assert run.result["proposal"]["version"] == 2
        await checkpointer.adelete_thread(graph_thread_id)
