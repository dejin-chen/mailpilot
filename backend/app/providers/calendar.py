"""日历 Provider 接口与 PostgreSQL 本地实现。"""

from datetime import datetime
from typing import Protocol
from uuid import UUID

from app.models.calendar import CalendarEvent
from app.repositories.calendar import CalendarRepository


class CalendarProvider(Protocol):
    """CalendarService 所依赖的日历存取能力合同。"""

    async def get_by_id(self, *, user_id: UUID, event_id: UUID) -> CalendarEvent | None: ...

    async def get_by_external_id(
        self,
        *,
        user_id: UUID,
        provider: str,
        external_id: str,
    ) -> CalendarEvent | None: ...

    async def get_by_idempotency_key(
        self,
        *,
        user_id: UUID,
        idempotency_key: str,
    ) -> CalendarEvent | None: ...

    async def list_events(
        self,
        *,
        user_id: UUID,
        window_start: datetime | None = None,
        window_end: datetime | None = None,
        offset: int = 0,
        limit: int = 20,
    ) -> list[CalendarEvent]: ...

    async def count_events(
        self,
        *,
        user_id: UUID,
        window_start: datetime | None = None,
        window_end: datetime | None = None,
    ) -> int: ...

    async def find_conflicts(
        self,
        *,
        user_id: UUID,
        start_at: datetime,
        end_at: datetime,
        exclude_event_id: UUID | None = None,
    ) -> list[CalendarEvent]: ...

    async def add(self, event: CalendarEvent) -> CalendarEvent: ...


class LocalCalendarProvider:
    """通过 Repository 使用 PostgreSQL 模拟企业日历。"""

    def __init__(self, repository: CalendarRepository) -> None:
        self._repository = repository

    async def get_by_id(self, *, user_id: UUID, event_id: UUID) -> CalendarEvent | None:
        return await self._repository.get_by_id(user_id=user_id, event_id=event_id)

    async def get_by_external_id(
        self,
        *,
        user_id: UUID,
        provider: str,
        external_id: str,
    ) -> CalendarEvent | None:
        return await self._repository.get_by_external_id(
            user_id=user_id,
            provider=provider,
            external_id=external_id,
        )

    async def get_by_idempotency_key(
        self,
        *,
        user_id: UUID,
        idempotency_key: str,
    ) -> CalendarEvent | None:
        return await self._repository.get_by_idempotency_key(
            user_id=user_id,
            idempotency_key=idempotency_key,
        )

    async def list_events(
        self,
        *,
        user_id: UUID,
        window_start: datetime | None = None,
        window_end: datetime | None = None,
        offset: int = 0,
        limit: int = 20,
    ) -> list[CalendarEvent]:
        return await self._repository.list_events(
            user_id=user_id,
            window_start=window_start,
            window_end=window_end,
            offset=offset,
            limit=limit,
        )

    async def count_events(
        self,
        *,
        user_id: UUID,
        window_start: datetime | None = None,
        window_end: datetime | None = None,
    ) -> int:
        return await self._repository.count_events(
            user_id=user_id,
            window_start=window_start,
            window_end=window_end,
        )

    async def find_conflicts(
        self,
        *,
        user_id: UUID,
        start_at: datetime,
        end_at: datetime,
        exclude_event_id: UUID | None = None,
    ) -> list[CalendarEvent]:
        return await self._repository.find_conflicts(
            user_id=user_id,
            start_at=start_at,
            end_at=end_at,
            exclude_event_id=exclude_event_id,
        )

    async def add(self, event: CalendarEvent) -> CalendarEvent:
        return await self._repository.add(event)
