"""AgentRun Service 状态机和事务单元测试。"""

from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from app.models.agent_run import AgentRun, AgentRunStatus
from app.repositories.agent_run import AgentRunRepository
from app.repositories.audit import AuditRepository
from app.repositories.email import EmailRepository
from app.schemas.agent_run import AgentRunCreate
from app.services.agent_run import AgentRunService
from app.services.exceptions import AgentRunInvalidTransitionError
from sqlalchemy.ext.asyncio import AsyncSession


@pytest.mark.asyncio
async def test_create_run_checks_email_and_writes_audit_in_one_commit() -> None:
    session = AsyncMock(spec=AsyncSession)
    runs = AsyncMock(spec=AgentRunRepository)
    emails = AsyncMock(spec=EmailRepository)
    audits = AsyncMock(spec=AuditRepository)
    emails.get_thread_by_id.return_value = object()
    runs.get_by_graph_thread_id.return_value = None
    run_id = uuid4()

    async def add_run(run: AgentRun) -> AgentRun:
        run.id = run_id
        return run

    runs.add.side_effect = add_run
    service = AgentRunService(session, runs, emails, audits)
    user_id = uuid4()
    data = AgentRunCreate(email_thread_id=uuid4(), graph_thread_id="graph-001")

    run = await service.create_run(user_id=user_id, data=data, request_id="request-001")

    assert run.id == run_id
    assert run.user_id == user_id
    assert run.status is AgentRunStatus.PENDING
    emails.get_thread_by_id.assert_awaited_once_with(
        user_id=user_id,
        thread_id=data.email_thread_id,
    )
    audits.add.assert_awaited_once()
    session.commit.assert_awaited_once()
    session.rollback.assert_not_awaited()


@pytest.mark.asyncio
async def test_transition_status_sets_started_time_and_audit() -> None:
    session = AsyncMock(spec=AsyncSession)
    runs = AsyncMock(spec=AgentRunRepository)
    audits = AsyncMock(spec=AuditRepository)
    run = AgentRun(
        id=uuid4(),
        user_id=uuid4(),
        email_thread_id=uuid4(),
        graph_thread_id="graph-002",
        status=AgentRunStatus.PENDING,
    )
    runs.get_for_update.return_value = run
    service = AgentRunService(
        session,
        run_repository=runs,
        email_repository=AsyncMock(spec=EmailRepository),
        audit_repository=audits,
    )

    updated = await service.transition_status(
        user_id=run.user_id,
        run_id=run.id,
        target_status=AgentRunStatus.RUNNING,
        current_node="load_email",
    )

    assert updated.status is AgentRunStatus.RUNNING
    assert updated.started_at is not None
    assert updated.current_node == "load_email"
    audits.add.assert_awaited_once()
    session.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_terminal_run_rejects_another_transition_and_rolls_back() -> None:
    session = AsyncMock(spec=AsyncSession)
    runs = AsyncMock(spec=AgentRunRepository)
    run = AgentRun(
        id=uuid4(),
        user_id=uuid4(),
        email_thread_id=uuid4(),
        graph_thread_id="graph-003",
        status=AgentRunStatus.COMPLETED,
    )
    runs.get_for_update.return_value = run
    service = AgentRunService(
        session,
        run_repository=runs,
        email_repository=AsyncMock(spec=EmailRepository),
        audit_repository=AsyncMock(spec=AuditRepository),
    )

    with pytest.raises(AgentRunInvalidTransitionError):
        await service.transition_status(
            user_id=run.user_id,
            run_id=run.id,
            target_status=AgentRunStatus.RUNNING,
            current_node="should_not_run",
        )

    session.commit.assert_not_awaited()
    session.rollback.assert_awaited_once()
