"""AgentRun、审批、审计和用户隔离的真实 PostgreSQL 测试。"""

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from app.models.agent_run import AgentRunStatus
from app.models.approval import ApprovalAction, ApprovalStatus
from app.repositories.audit import AuditRepository
from app.schemas.agent_run import AgentRunCreate
from app.schemas.approval import ApprovalDecision, ApprovalRequestCreate
from app.schemas.email import EmailMessageImport
from app.schemas.user import UserCreate
from app.services.agent_run import AgentRunService
from app.services.approval import ApprovalService
from app.services.email import EmailService
from app.services.exceptions import (
    ApprovalAlreadyDecidedError,
    ApprovalRequestNotFoundError,
)
from app.services.user import UserService
from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = pytest.mark.integration


async def _create_user_and_run(
    session: AsyncSession,
    *,
    email: str,
    suffix: str,
) -> tuple:
    user = await UserService(session).create_user(
        UserCreate(
            email=email,
            password="approval-integration-password",
            full_name="审批集成测试用户",
            timezone="Asia/Shanghai",
        )
    )
    imported = await EmailService(session).import_inbound_email(
        user_id=user.id,
        data=EmailMessageImport(
            provider="local",
            thread_external_id=f"approval-thread-{suffix}",
            message_external_id=f"approval-message-{suffix}",
            subject="审批测试邮件",
            sender="manager@example.com",
            recipients=[email],
            body_text="请确认并回复。",
            sent_at=datetime(2026, 7, 24, 1, 0, tzinfo=UTC),
        ),
    )
    run = await AgentRunService(session).create_run(
        user_id=user.id,
        data=AgentRunCreate(
            email_thread_id=imported.thread.id,
            graph_thread_id=f"graph-{suffix}-{uuid4()}",
        ),
    )
    await AgentRunService(session).transition_status(
        user_id=user.id,
        run_id=run.id,
        target_status=AgentRunStatus.RUNNING,
        current_node="build_plan",
    )
    return user, run


async def test_approval_is_idempotent_isolated_and_audited(
    integration_session: AsyncSession,
) -> None:
    owner, run = await _create_user_and_run(
        integration_session,
        email="approval-owner@example.com",
        suffix="owner",
    )
    stranger, _ = await _create_user_and_run(
        integration_session,
        email="approval-stranger@example.com",
        suffix="stranger",
    )
    service = ApprovalService(integration_session)
    data = ApprovalRequestCreate(
        agent_run_id=run.id,
        action=ApprovalAction.SEND_EMAIL,
        proposed_arguments={"draft_message_id": str(uuid4())},
        idempotency_key=f"approval-{run.id}-send-v1",
    )

    created = await service.create_request(user_id=owner.id, data=data)
    reused = await service.create_request(user_id=owner.id, data=data)

    assert created.id == reused.id
    assert created.status is ApprovalStatus.PENDING
    assert (
        await AgentRunService(integration_session).get_run(
            user_id=owner.id,
            run_id=run.id,
        )
    ).status is AgentRunStatus.WAITING_APPROVAL
    with pytest.raises(ApprovalRequestNotFoundError):
        await service.get_request(user_id=stranger.id, approval_id=created.id)

    events = await AuditRepository(integration_session).list_events(user_id=owner.id)
    actions = [event.action for event in events]
    assert "agent_run.created" in actions
    assert "agent_run.status_changed" in actions
    assert actions.count("approval.created") == 1


async def test_approval_decision_is_single_use_and_rejection_cancels_run(
    integration_session: AsyncSession,
) -> None:
    user, run = await _create_user_and_run(
        integration_session,
        email="approval-reject@example.com",
        suffix="reject",
    )
    service = ApprovalService(integration_session)
    approval = await service.create_request(
        user_id=user.id,
        data=ApprovalRequestCreate(
            agent_run_id=run.id,
            action=ApprovalAction.CANCEL_EVENT,
            proposed_arguments={"event_id": str(uuid4())},
            idempotency_key=f"approval-{run.id}-cancel-v1",
        ),
    )

    rejected = await service.decide(
        user_id=user.id,
        approval_id=approval.id,
        actor_user_id=user.id,
        decision=ApprovalDecision(
            status=ApprovalStatus.REJECTED,
            feedback="当前会议不应取消",
        ),
    )

    assert rejected.status is ApprovalStatus.REJECTED
    run_after_rejection = await AgentRunService(integration_session).get_run(
        user_id=user.id,
        run_id=run.id,
    )
    assert run_after_rejection.status is AgentRunStatus.CANCELLED
    with pytest.raises(ApprovalAlreadyDecidedError):
        await service.decide(
            user_id=user.id,
            approval_id=approval.id,
            actor_user_id=user.id,
            decision=ApprovalDecision(status=ApprovalStatus.APPROVED),
        )
