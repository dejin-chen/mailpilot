"""日历业务单元测试。"""

from datetime import UTC, datetime
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from app.models.calendar import CalendarEvent, CalendarEventStatus
from app.repositories.calendar import CalendarRepository
from app.schemas.calendar import CalendarEventImport
from app.services.calendar import CalendarService
from app.services.exceptions import (
    CalendarEventAlreadyImportedError,
    CalendarEventNotFoundError,
    InvalidCalendarRangeError,
)
from sqlalchemy.ext.asyncio import AsyncSession


def _event_import(**changes: object) -> CalendarEventImport:
    payload: dict[str, object] = {
        "provider": "local",
        "external_id": "event-001",
        "title": "项目周会",
        "description": "讨论项目进度",
        "start_at": datetime(2026, 7, 20, 2, 0, tzinfo=UTC),
        "end_at": datetime(2026, 7, 20, 3, 0, tzinfo=UTC),
        "timezone": "Asia/Shanghai",
        "attendees": ["team@example.com", "team@example.com"],
        "idempotency_key": "calendar-import-001",
    }
    payload.update(changes)
    return CalendarEventImport.model_validate(payload)


@pytest.mark.asyncio
async def test_import_event_deduplicates_attendees_and_commits() -> None:
    session = AsyncMock(spec=AsyncSession)
    provider = AsyncMock(spec=CalendarRepository)
    provider.get_by_external_id.return_value = None
    provider.get_by_idempotency_key.return_value = None
    service = CalendarService(session, provider)

    event = await service.import_event(user_id=uuid4(), data=_event_import())

    assert event.status is CalendarEventStatus.CONFIRMED
    assert event.attendees == ["team@example.com"]
    provider.add.assert_awaited_once_with(event)
    session.commit.assert_awaited_once()
    session.rollback.assert_not_awaited()


@pytest.mark.asyncio
async def test_import_duplicate_event_rolls_back() -> None:
    session = AsyncMock(spec=AsyncSession)
    provider = AsyncMock(spec=CalendarRepository)
    provider.get_by_external_id.return_value = object()
    service = CalendarService(session, provider)

    with pytest.raises(CalendarEventAlreadyImportedError):
        await service.import_event(user_id=uuid4(), data=_event_import())

    provider.add.assert_not_awaited()
    session.commit.assert_not_awaited()
    session.rollback.assert_awaited_once()


@pytest.mark.asyncio
async def test_get_event_hides_missing_or_other_users_event() -> None:
    session = AsyncMock(spec=AsyncSession)
    provider = AsyncMock(spec=CalendarRepository)
    provider.get_by_id.return_value = None
    service = CalendarService(session, provider)

    with pytest.raises(CalendarEventNotFoundError):
        await service.get_event(user_id=uuid4(), event_id=uuid4())


@pytest.mark.asyncio
async def test_list_event_page_reads_items_then_total() -> None:
    session = AsyncMock(spec=AsyncSession)
    provider = AsyncMock(spec=CalendarRepository)
    provider.list_events.return_value = [object()]
    provider.count_events.return_value = 3
    service = CalendarService(session, provider)

    page = await service.list_event_page(user_id=uuid4(), offset=1, limit=1)

    assert len(page.items) == 1
    assert page.total == 3
    assert page.offset == 1
    provider.list_events.assert_awaited_once()
    provider.count_events.assert_awaited_once()


@pytest.mark.asyncio
async def test_find_conflicts_rejects_invalid_range_before_query() -> None:
    session = AsyncMock(spec=AsyncSession)
    provider = AsyncMock(spec=CalendarRepository)
    service = CalendarService(session, provider)
    moment = datetime(2026, 7, 20, 2, 0, tzinfo=UTC)

    with pytest.raises(InvalidCalendarRangeError):
        await service.find_conflicts(user_id=uuid4(), start_at=moment, end_at=moment)

    provider.find_conflicts.assert_not_awaited()


@pytest.mark.asyncio
async def test_find_available_slots_skips_busy_time_and_keeps_adjacent_slot() -> None:
    session = AsyncMock(spec=AsyncSession)
    provider = AsyncMock(spec=CalendarRepository)
    busy_start = datetime(2026, 7, 20, 10, 0, tzinfo=UTC)
    busy_end = datetime(2026, 7, 20, 11, 0, tzinfo=UTC)
    provider.find_conflicts.return_value = [
        type("BusyEvent", (), {"start_at": busy_start, "end_at": busy_end})()
    ]
    service = CalendarService(session, provider)

    slots = await service.find_available_slots(
        user_id=uuid4(),
        window_start=datetime(2026, 7, 20, 9, 0, tzinfo=UTC),
        window_end=datetime(2026, 7, 20, 12, 0, tzinfo=UTC),
        duration_minutes=60,
        step_minutes=60,
        limit=5,
    )

    assert [(slot.start_at.hour, slot.end_at.hour) for slot in slots] == [(9, 10), (11, 12)]


@pytest.mark.asyncio
async def test_create_event_checks_conflicts_and_uses_idempotency_key() -> None:
    session = AsyncMock(spec=AsyncSession)
    provider = AsyncMock(spec=CalendarRepository)
    provider.get_by_idempotency_key.return_value = None
    provider.find_conflicts.return_value = []
    service = CalendarService(session, provider)
    start_at = datetime(2026, 7, 25, 6, 0, tzinfo=UTC)
    end_at = datetime(2026, 7, 25, 7, 0, tzinfo=UTC)

    result = await service.create_event(
        user_id=uuid4(),
        title="项目评审",
        start_at=start_at,
        end_at=end_at,
        attendees=["team@example.com", "team@example.com"],
        timezone="Asia/Shanghai",
        idempotency_key="write-create-event-001",
    )

    assert result.reused is False
    assert result.event.attendees == ["team@example.com"]
    assert result.event.idempotency_key == "write-create-event-001"
    provider.find_conflicts.assert_awaited_once()
    provider.add.assert_awaited_once_with(result.event)
    session.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_reschedule_same_time_is_idempotent_without_database_write() -> None:
    session = AsyncMock(spec=AsyncSession)
    provider = AsyncMock(spec=CalendarRepository)
    start_at = datetime(2026, 7, 25, 6, 0, tzinfo=UTC)
    end_at = datetime(2026, 7, 25, 7, 0, tzinfo=UTC)
    event = CalendarEvent(
        id=uuid4(),
        user_id=uuid4(),
        provider="local",
        external_id="event-reschedule-001",
        title="项目评审",
        description="",
        start_at=start_at,
        end_at=end_at,
        timezone="Asia/Shanghai",
        attendees=[],
        status=CalendarEventStatus.CONFIRMED,
    )
    provider.get_by_id.return_value = event
    service = CalendarService(session, provider)

    result = await service.reschedule_event(
        user_id=event.user_id,
        event_id=event.id,
        new_start_at=start_at,
        new_end_at=end_at,
    )

    assert result.reused is True
    provider.find_conflicts.assert_not_awaited()
    session.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_cancel_event_is_idempotent() -> None:
    session = AsyncMock(spec=AsyncSession)
    provider = AsyncMock(spec=CalendarRepository)
    event = CalendarEvent(
        id=uuid4(),
        user_id=uuid4(),
        provider="local",
        external_id="event-cancel-001",
        title="已取消会议",
        description="",
        start_at=datetime(2026, 7, 25, 6, 0, tzinfo=UTC),
        end_at=datetime(2026, 7, 25, 7, 0, tzinfo=UTC),
        timezone="Asia/Shanghai",
        attendees=[],
        status=CalendarEventStatus.CANCELLED,
    )
    provider.get_by_id.return_value = event
    service = CalendarService(session, provider)

    result = await service.cancel_event(user_id=event.user_id, event_id=event.id)

    assert result.reused is True
    assert result.event.status is CalendarEventStatus.CANCELLED
    session.commit.assert_awaited_once()
