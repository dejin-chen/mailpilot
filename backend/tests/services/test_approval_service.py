"""审批 Service 幂等和决定状态机单元测试。"""

from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from app.models.agent_run import AgentRun, AgentRunStatus
from app.models.approval import ApprovalAction, ApprovalRequest, ApprovalStatus
from app.repositories.agent_run import AgentRunRepository
from app.repositories.approval import ApprovalRepository
from app.repositories.audit import AuditRepository
from app.schemas.approval import ApprovalDecision, ApprovalRequestCreate
from app.services.approval import ApprovalService
from app.services.exceptions import (
    ApprovalAlreadyDecidedError,
    ApprovalArgumentsInvalidError,
)
from sqlalchemy.ext.asyncio import AsyncSession


def _create_data(run_id) -> ApprovalRequestCreate:
    return ApprovalRequestCreate(
        agent_run_id=run_id,
        action=ApprovalAction.SEND_EMAIL,
        proposed_arguments={"draft_message_id": str(uuid4())},
        idempotency_key="approval-send-001",
    )


@pytest.mark.asyncio
async def test_create_request_marks_run_waiting_and_writes_audit() -> None:
    session = AsyncMock(spec=AsyncSession)
    approvals = AsyncMock(spec=ApprovalRepository)
    runs = AsyncMock(spec=AgentRunRepository)
    audits = AsyncMock(spec=AuditRepository)
    run = AgentRun(
        id=uuid4(),
        user_id=uuid4(),
        email_thread_id=uuid4(),
        graph_thread_id="approval-graph-001",
        status=AgentRunStatus.RUNNING,
    )
    approvals.get_by_idempotency_key.return_value = None
    runs.get_for_update.return_value = run

    async def add_approval(approval: ApprovalRequest) -> ApprovalRequest:
        approval.id = uuid4()
        return approval

    approvals.add.side_effect = add_approval
    service = ApprovalService(session, approvals, runs, audits)

    approval = await service.create_request(
        user_id=run.user_id,
        data=_create_data(run.id),
        request_id="approval-request-001",
    )

    assert approval.status is ApprovalStatus.PENDING
    assert run.status is AgentRunStatus.WAITING_APPROVAL
    assert run.current_node == "waiting_approval"
    audits.add.assert_awaited_once()
    session.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_same_idempotency_payload_reuses_existing_request() -> None:
    session = AsyncMock(spec=AsyncSession)
    approvals = AsyncMock(spec=ApprovalRepository)
    run_id = uuid4()
    data = _create_data(run_id)
    existing = ApprovalRequest(
        id=uuid4(),
        user_id=uuid4(),
        agent_run_id=run_id,
        action=data.action,
        proposed_arguments=dict(data.proposed_arguments),
        idempotency_key=data.idempotency_key,
        version=data.version,
        status=ApprovalStatus.PENDING,
    )
    approvals.get_by_idempotency_key.return_value = existing
    service = ApprovalService(
        session,
        approvals,
        AsyncMock(spec=AgentRunRepository),
        AsyncMock(spec=AuditRepository),
    )

    reused = await service.create_request(user_id=existing.user_id, data=data)

    assert reused is existing
    approvals.add.assert_not_awaited()
    session.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_approve_with_modified_arguments_is_recorded_once() -> None:
    session = AsyncMock(spec=AsyncSession)
    approvals = AsyncMock(spec=ApprovalRepository)
    runs = AsyncMock(spec=AgentRunRepository)
    audits = AsyncMock(spec=AuditRepository)
    user_id = uuid4()
    run = AgentRun(
        id=uuid4(),
        user_id=user_id,
        email_thread_id=uuid4(),
        graph_thread_id="approval-graph-002",
        status=AgentRunStatus.WAITING_APPROVAL,
    )
    original_draft_id = uuid4()
    modified_draft_id = uuid4()
    approval = ApprovalRequest(
        id=uuid4(),
        user_id=user_id,
        agent_run_id=run.id,
        action=ApprovalAction.SEND_EMAIL,
        proposed_arguments={"draft_message_id": str(original_draft_id)},
        idempotency_key="approval-send-002",
        status=ApprovalStatus.PENDING,
    )
    approvals.get_for_update.return_value = approval
    runs.get_for_update.return_value = run
    service = ApprovalService(session, approvals, runs, audits)
    actor_user_id = uuid4()

    decided = await service.decide(
        user_id=user_id,
        approval_id=approval.id,
        actor_user_id=actor_user_id,
        decision=ApprovalDecision(
            status=ApprovalStatus.APPROVED,
            modified_arguments={"draft_message_id": str(modified_draft_id)},
        ),
    )

    assert decided.status is ApprovalStatus.APPROVED
    assert decided.modified_arguments == {"draft_message_id": str(modified_draft_id)}
    assert decided.decided_by_user_id == actor_user_id
    assert decided.decided_at is not None
    audits.add.assert_awaited_once()
    session.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_non_pending_approval_cannot_be_decided_again() -> None:
    session = AsyncMock(spec=AsyncSession)
    approvals = AsyncMock(spec=ApprovalRepository)
    approval = ApprovalRequest(
        id=uuid4(),
        user_id=uuid4(),
        agent_run_id=uuid4(),
        action=ApprovalAction.CANCEL_EVENT,
        proposed_arguments={"event_id": str(uuid4())},
        idempotency_key="approval-cancel-001",
        status=ApprovalStatus.APPROVED,
    )
    approvals.get_for_update.return_value = approval
    service = ApprovalService(
        session,
        approvals,
        AsyncMock(spec=AgentRunRepository),
        AsyncMock(spec=AuditRepository),
    )

    with pytest.raises(ApprovalAlreadyDecidedError):
        await service.decide(
            user_id=approval.user_id,
            approval_id=approval.id,
            actor_user_id=approval.user_id,
            decision=ApprovalDecision(status=ApprovalStatus.REJECTED),
        )

    session.commit.assert_not_awaited()
    session.rollback.assert_awaited_once()


@pytest.mark.asyncio
async def test_same_terminal_decision_is_idempotently_reused() -> None:
    """响应丢失后重试同一决定，不重复写审批审计。"""

    session = AsyncMock(spec=AsyncSession)
    approvals = AsyncMock(spec=ApprovalRepository)
    actor_user_id = uuid4()
    approval = ApprovalRequest(
        id=uuid4(),
        user_id=actor_user_id,
        agent_run_id=uuid4(),
        action=ApprovalAction.SEND_EMAIL,
        proposed_arguments={"draft_message_id": str(uuid4())},
        idempotency_key="approval-retry-001",
        status=ApprovalStatus.APPROVED,
        decided_by_user_id=actor_user_id,
    )
    approvals.get_for_update.return_value = approval
    runs = AsyncMock(spec=AgentRunRepository)
    audits = AsyncMock(spec=AuditRepository)
    service = ApprovalService(session, approvals, runs, audits)

    reused = await service.decide(
        user_id=actor_user_id,
        approval_id=approval.id,
        actor_user_id=actor_user_id,
        decision=ApprovalDecision(status=ApprovalStatus.APPROVED),
    )

    assert reused is approval
    session.commit.assert_awaited_once()
    runs.get_for_update.assert_not_awaited()
    audits.add.assert_not_awaited()


@pytest.mark.asyncio
async def test_executed_approval_still_reuses_original_approved_decision() -> None:
    """内部执行状态不能让浏览器重试被误判为第二次人工决定。"""

    session = AsyncMock(spec=AsyncSession)
    approvals = AsyncMock(spec=ApprovalRepository)
    actor_id = uuid4()
    approval = ApprovalRequest(
        id=uuid4(),
        user_id=actor_id,
        agent_run_id=uuid4(),
        action=ApprovalAction.SEND_EMAIL,
        proposed_arguments={"draft_message_id": str(uuid4())},
        idempotency_key="approval-executed-retry",
        status=ApprovalStatus.EXECUTED,
        decided_by_user_id=actor_id,
    )
    approvals.get_for_update.return_value = approval
    service = ApprovalService(
        session,
        approvals,
        AsyncMock(spec=AgentRunRepository),
        AsyncMock(spec=AuditRepository),
    )

    result = await service.decide(
        user_id=actor_id,
        approval_id=approval.id,
        actor_user_id=actor_id,
        decision=ApprovalDecision(status=ApprovalStatus.APPROVED),
    )

    assert result is approval
    session.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_modified_arguments_must_match_action_schema() -> None:
    session = AsyncMock(spec=AsyncSession)
    approvals = AsyncMock(spec=ApprovalRepository)
    approval = ApprovalRequest(
        id=uuid4(),
        user_id=uuid4(),
        agent_run_id=uuid4(),
        action=ApprovalAction.CANCEL_EVENT,
        proposed_arguments={"event_id": str(uuid4())},
        idempotency_key="approval-invalid-modification-001",
        status=ApprovalStatus.PENDING,
    )
    approvals.get_for_update.return_value = approval
    service = ApprovalService(
        session,
        approvals,
        AsyncMock(spec=AgentRunRepository),
        AsyncMock(spec=AuditRepository),
    )

    with pytest.raises(ApprovalArgumentsInvalidError):
        await service.decide(
            user_id=approval.user_id,
            approval_id=approval.id,
            actor_user_id=approval.user_id,
            decision=ApprovalDecision(
                status=ApprovalStatus.APPROVED,
                modified_arguments={"draft_message_id": str(uuid4())},
            ),
        )

    session.rollback.assert_awaited_once()
