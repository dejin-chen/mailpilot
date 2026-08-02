"""本地 Provider 委托测试。"""

from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from app.providers.calendar import LocalCalendarProvider
from app.providers.mail import LocalMailProvider
from app.repositories.calendar import CalendarRepository
from app.repositories.email import EmailRepository


@pytest.mark.asyncio
async def test_local_mail_provider_keeps_user_scope() -> None:
    repository = AsyncMock(spec=EmailRepository)
    provider = LocalMailProvider(repository)
    user_id = uuid4()

    await provider.list_threads(user_id=user_id, offset=2, limit=5)

    repository.list_threads.assert_awaited_once_with(user_id=user_id, offset=2, limit=5)


@pytest.mark.asyncio
async def test_local_calendar_provider_keeps_conflict_parameters() -> None:
    repository = AsyncMock(spec=CalendarRepository)
    provider = LocalCalendarProvider(repository)
    user_id = uuid4()
    event_id = uuid4()

    await provider.get_by_id(user_id=user_id, event_id=event_id)

    repository.get_by_id.assert_awaited_once_with(user_id=user_id, event_id=event_id)
