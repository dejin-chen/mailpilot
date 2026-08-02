"""日历事件数据访问。"""

from datetime import datetime
from uuid import UUID

from sqlalchemy import ColumnElement, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.calendar import CalendarEvent, CalendarEventStatus


class CalendarRepository:
    """封装日历查询和写入，所有业务读取都包含 user_id。"""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_by_id(self, *, user_id: UUID, event_id: UUID) -> CalendarEvent | None:
        """查询当前用户的一条日历事件。"""

        statement = select(CalendarEvent).where(
            CalendarEvent.id == event_id,
            CalendarEvent.user_id == user_id,
        )
        return await self._session.scalar(statement)

    async def get_by_external_id(
        self,
        *,
        user_id: UUID,
        provider: str,
        external_id: str,
    ) -> CalendarEvent | None:
        """按用户、Provider 和外部 ID 查询事件。"""

        statement = select(CalendarEvent).where(
            CalendarEvent.user_id == user_id,
            CalendarEvent.provider == provider,
            CalendarEvent.external_id == external_id,
        )
        return await self._session.scalar(statement)

    async def get_by_idempotency_key(
        self,
        *,
        user_id: UUID,
        idempotency_key: str,
    ) -> CalendarEvent | None:
        """按当前用户的幂等键查询事件。"""

        statement = select(CalendarEvent).where(
            CalendarEvent.user_id == user_id,
            CalendarEvent.idempotency_key == idempotency_key,
        )
        return await self._session.scalar(statement)

    async def list_events(
        self,
        *,
        user_id: UUID,
        window_start: datetime | None = None,
        window_end: datetime | None = None,
        offset: int = 0,
        limit: int = 20,
    ) -> list[CalendarEvent]:
        """查询当前用户事件；提供窗口时只返回与窗口重叠的事件。"""

        statement = (
            select(CalendarEvent)
            .where(
                CalendarEvent.user_id == user_id,
                *self._window_conditions(window_start=window_start, window_end=window_end),
            )
            .order_by(CalendarEvent.start_at.asc(), CalendarEvent.id.asc())
            .offset(offset)
            .limit(limit)
        )
        result = await self._session.scalars(statement)
        return list(result.all())

    async def count_events(
        self,
        *,
        user_id: UUID,
        window_start: datetime | None = None,
        window_end: datetime | None = None,
    ) -> int:
        """统计当前用户在可选时间窗口内的事件数。"""

        statement = (
            select(func.count())
            .select_from(CalendarEvent)
            .where(
                CalendarEvent.user_id == user_id,
                *self._window_conditions(window_start=window_start, window_end=window_end),
            )
        )
        return int(await self._session.scalar(statement) or 0)

    async def find_conflicts(
        self,
        *,
        user_id: UUID,
        start_at: datetime,
        end_at: datetime,
        exclude_event_id: UUID | None = None,
    ) -> list[CalendarEvent]:
        """查找与给定半开时间区间重叠且未取消的事件。"""

        conditions: list[ColumnElement[bool]] = [
            CalendarEvent.user_id == user_id,
            CalendarEvent.status != CalendarEventStatus.CANCELLED,
            CalendarEvent.start_at < end_at,
            CalendarEvent.end_at > start_at,
        ]
        if exclude_event_id is not None:
            conditions.append(CalendarEvent.id != exclude_event_id)
        statement = (
            select(CalendarEvent)
            .where(*conditions)
            .order_by(CalendarEvent.start_at.asc(), CalendarEvent.id.asc())
        )
        result = await self._session.scalars(statement)
        return list(result.all())

    async def add(self, event: CalendarEvent) -> CalendarEvent:
        """把事件加入 Session 并 flush，事务提交由 Service 决定。"""

        self._session.add(event)
        await self._session.flush()
        return event

    @staticmethod
    def _window_conditions(
        *,
        window_start: datetime | None,
        window_end: datetime | None,
    ) -> list[ColumnElement[bool]]:
        """生成事件与查询窗口重叠的 SQL 条件。"""

        conditions: list[ColumnElement[bool]] = []
        if window_start is not None:
            conditions.append(CalendarEvent.end_at > window_start)
        if window_end is not None:
            conditions.append(CalendarEvent.start_at < window_end)
        return conditions
