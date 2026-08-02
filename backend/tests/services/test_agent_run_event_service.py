"""Agent 执行事件 Service 与安全摘要单元测试。"""

from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from app.agent.schemas import (
    EmailAction,
    EmailCategory,
    EmailClassification,
    EmailPriority,
)
from app.models.agent_run import AgentRun
from app.models.agent_run_event import AgentRunEvent, AgentRunEventType
from app.repositories.agent_run import AgentRunRepository
from app.repositories.agent_run_event import AgentRunEventRepository
from app.services.agent_run_event import AgentRunEventService, build_node_event_payload
from app.services.exceptions import AgentRunNotFoundError
from sqlalchemy.ext.asyncio import AsyncSession


@pytest.mark.asyncio
async def test_append_uses_locked_run_and_next_sequence() -> None:
    session = AsyncMock(spec=AsyncSession)
    events = AsyncMock(spec=AgentRunEventRepository)
    runs = AsyncMock(spec=AgentRunRepository)
    run = AgentRun(id=uuid4(), user_id=uuid4())
    runs.get_for_update.return_value = run
    events.next_sequence.return_value = 3

    async def add_event(event: AgentRunEvent) -> AgentRunEvent:
        event.id = uuid4()
        return event

    events.add.side_effect = add_event
    service = AgentRunEventService(session, events, runs)

    event = await service.append(
        user_id=run.user_id,
        agent_run_id=run.id,
        event_type=AgentRunEventType.NODE_COMPLETED,
        node_name="classify_email",
        payload=build_node_event_payload(
            node_name="classify_email",
            update={
                "classification": EmailClassification(
                    action=EmailAction.REPLY,
                    priority=EmailPriority.HIGH,
                    category=EmailCategory.REQUEST,
                    summary="摘要不会进入事件",
                    reason="原因不会进入事件",
                    confidence=0.95,
                ),
                "email_body": "这段不可信邮件正文不能进入事件",
            },
        ),
    )

    assert event.sequence == 3
    assert event.payload["action"] == "reply"
    assert "email_body" not in event.payload
    assert "summary" not in event.payload
    assert session.commit.await_count == 1


@pytest.mark.asyncio
async def test_append_rejects_other_users_run() -> None:
    session = AsyncMock(spec=AsyncSession)
    runs = AsyncMock(spec=AgentRunRepository)
    runs.get_for_update.return_value = None
    service = AgentRunEventService(
        session,
        AsyncMock(spec=AgentRunEventRepository),
        runs,
    )

    with pytest.raises(AgentRunNotFoundError):
        await service.append(
            user_id=uuid4(),
            agent_run_id=uuid4(),
            event_type=AgentRunEventType.RUN_STARTED,
        )

    session.rollback.assert_awaited_once()
