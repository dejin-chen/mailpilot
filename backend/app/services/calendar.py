"""日历事件导入、查询与冲突判断业务。"""

from dataclasses import dataclass
from datetime import datetime, timedelta
from uuid import UUID, uuid4

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.calendar import CalendarEvent, CalendarEventStatus
from app.providers.calendar import CalendarProvider, LocalCalendarProvider
from app.repositories.calendar import CalendarRepository
from app.schemas.calendar import CalendarEventImport
from app.services.exceptions import (
    CalendarEventAlreadyImportedError,
    CalendarEventNotFoundError,
    CalendarImportConflictError,
    CalendarScheduleConflictError,
    CalendarWriteConflictError,
    InvalidCalendarRangeError,
)


@dataclass(frozen=True, slots=True)
class CalendarEventPage:
    """日历事件分页结果。"""

    items: list[CalendarEvent]
    total: int
    offset: int
    limit: int


@dataclass(frozen=True, slots=True)
class AvailableTimeSlot:
    """CalendarService 计算得到的一个可用候选时间段。"""

    start_at: datetime
    end_at: datetime


@dataclass(frozen=True, slots=True)
class CalendarWriteResult:
    """日历写操作结果；reused 表示没有产生第二次副作用。"""

    event: CalendarEvent
    reused: bool


class CalendarService:
    """执行日历业务规则，并负责导入事务提交和回滚。"""

    def __init__(
        self,
        session: AsyncSession,
        provider: CalendarProvider | None = None,
    ) -> None:
        self._session = session
        self._provider = (
            provider if provider is not None else LocalCalendarProvider(CalendarRepository(session))
        )

    async def import_event(
        self,
        *,
        user_id: UUID,
        data: CalendarEventImport,
    ) -> CalendarEvent:
        """幂等地导入本地测试事件；并发冲突时最多重试一次。"""

        for attempt in range(2):
            try:
                if await self._event_exists(user_id=user_id, data=data):
                    raise CalendarEventAlreadyImportedError

                event = CalendarEvent(
                    user_id=user_id,
                    provider=data.provider,
                    external_id=data.external_id,
                    title=data.title,
                    description=data.description,
                    start_at=data.start_at,
                    end_at=data.end_at,
                    timezone=data.timezone,
                    attendees=sorted({str(address) for address in data.attendees}),
                    location=data.location,
                    is_all_day=data.is_all_day,
                    status=data.status,
                    idempotency_key=data.idempotency_key,
                )
                await self._provider.add(event)
                await self._session.commit()
                return event
            except CalendarEventAlreadyImportedError:
                await self._session.rollback()
                raise
            except IntegrityError as exc:
                await self._session.rollback()
                if await self._event_exists(user_id=user_id, data=data):
                    raise CalendarEventAlreadyImportedError from exc
                if attempt == 1:
                    raise CalendarImportConflictError from exc
            except Exception:
                await self._session.rollback()
                raise

        raise CalendarImportConflictError

    async def get_event(self, *, user_id: UUID, event_id: UUID) -> CalendarEvent:
        """读取当前用户的一条事件，不暴露其他用户是否拥有该 UUID。"""

        event = await self._provider.get_by_id(user_id=user_id, event_id=event_id)
        if event is None:
            raise CalendarEventNotFoundError
        return event

    async def list_event_page(
        self,
        *,
        user_id: UUID,
        window_start: datetime | None = None,
        window_end: datetime | None = None,
        offset: int = 0,
        limit: int = 20,
    ) -> CalendarEventPage:
        """分页读取事件，可按相交时间窗口过滤。"""

        self._validate_optional_window(window_start=window_start, window_end=window_end)
        items = await self._provider.list_events(
            user_id=user_id,
            window_start=window_start,
            window_end=window_end,
            offset=offset,
            limit=limit,
        )
        total = await self._provider.count_events(
            user_id=user_id,
            window_start=window_start,
            window_end=window_end,
        )
        return CalendarEventPage(items=items, total=total, offset=offset, limit=limit)

    async def find_conflicts(
        self,
        *,
        user_id: UUID,
        start_at: datetime,
        end_at: datetime,
        exclude_event_id: UUID | None = None,
    ) -> list[CalendarEvent]:
        """查询给定时间范围内未取消的冲突事件。"""

        self._validate_required_window(start_at=start_at, end_at=end_at)
        return await self._provider.find_conflicts(
            user_id=user_id,
            start_at=start_at,
            end_at=end_at,
            exclude_event_id=exclude_event_id,
        )

    async def find_available_slots(
        self,
        *,
        user_id: UUID,
        window_start: datetime,
        window_end: datetime,
        duration_minutes: int,
        step_minutes: int = 30,
        limit: int = 5,
    ) -> list[AvailableTimeSlot]:
        """在给定窗口内按固定步长寻找不与已有日程冲突的候选时段。"""

        self._validate_required_window(start_at=window_start, end_at=window_end)
        if not 1 <= duration_minutes <= 480 or not 5 <= step_minutes <= 120:
            raise InvalidCalendarRangeError
        if not 1 <= limit <= 20:
            raise InvalidCalendarRangeError

        conflicts = await self._provider.find_conflicts(
            user_id=user_id,
            start_at=window_start,
            end_at=window_end,
        )
        duration = timedelta(minutes=duration_minutes)
        step = timedelta(minutes=step_minutes)
        candidate_start = window_start
        slots: list[AvailableTimeSlot] = []

        while candidate_start + duration <= window_end and len(slots) < limit:
            candidate_end = candidate_start + duration
            overlaps = any(
                event.start_at < candidate_end and event.end_at > candidate_start
                for event in conflicts
            )
            if not overlaps:
                slots.append(AvailableTimeSlot(start_at=candidate_start, end_at=candidate_end))
            candidate_start += step

        return slots

    async def create_event(
        self,
        *,
        user_id: UUID,
        title: str,
        start_at: datetime,
        end_at: datetime,
        attendees: list[str],
        timezone: str,
        idempotency_key: str,
    ) -> CalendarWriteResult:
        """创建本地会议；相同幂等键和相同参数会复用已有事件。"""

        self._validate_required_window(start_at=start_at, end_at=end_at)
        existing = await self._provider.get_by_idempotency_key(
            user_id=user_id,
            idempotency_key=idempotency_key,
        )
        normalized_attendees = sorted(set(attendees))
        if existing is not None:
            if self._matches_created_event(
                existing,
                title=title,
                start_at=start_at,
                end_at=end_at,
                attendees=normalized_attendees,
                timezone=timezone,
            ):
                return CalendarWriteResult(event=existing, reused=True)
            raise CalendarWriteConflictError

        try:
            conflicts = await self._provider.find_conflicts(
                user_id=user_id,
                start_at=start_at,
                end_at=end_at,
            )
            if conflicts:
                raise CalendarScheduleConflictError
            event = CalendarEvent(
                user_id=user_id,
                provider="local",
                external_id=f"event-{uuid4()}",
                title=title,
                description="",
                start_at=start_at,
                end_at=end_at,
                timezone=timezone,
                attendees=normalized_attendees,
                location=None,
                is_all_day=False,
                status=CalendarEventStatus.CONFIRMED,
                idempotency_key=idempotency_key,
            )
            await self._provider.add(event)
            await self._session.commit()
            await self._session.refresh(event)
            return CalendarWriteResult(event=event, reused=False)
        except (CalendarScheduleConflictError, CalendarWriteConflictError):
            await self._session.rollback()
            raise
        except IntegrityError as exc:
            await self._session.rollback()
            existing = await self._provider.get_by_idempotency_key(
                user_id=user_id,
                idempotency_key=idempotency_key,
            )
            if existing is not None and self._matches_created_event(
                existing,
                title=title,
                start_at=start_at,
                end_at=end_at,
                attendees=normalized_attendees,
                timezone=timezone,
            ):
                return CalendarWriteResult(event=existing, reused=True)
            raise CalendarWriteConflictError from exc
        except Exception:
            await self._session.rollback()
            raise

    async def reschedule_event(
        self,
        *,
        user_id: UUID,
        event_id: UUID,
        new_start_at: datetime,
        new_end_at: datetime,
    ) -> CalendarWriteResult:
        """重新安排当前用户会议；相同时间重复设置不会产生额外变化。"""

        self._validate_required_window(start_at=new_start_at, end_at=new_end_at)
        try:
            event = await self._provider.get_by_id(user_id=user_id, event_id=event_id)
            if event is None:
                raise CalendarEventNotFoundError
            if event.status is CalendarEventStatus.CANCELLED:
                raise CalendarWriteConflictError
            if event.start_at == new_start_at and event.end_at == new_end_at:
                return CalendarWriteResult(event=event, reused=True)
            conflicts = await self._provider.find_conflicts(
                user_id=user_id,
                start_at=new_start_at,
                end_at=new_end_at,
                exclude_event_id=event.id,
            )
            if conflicts:
                raise CalendarScheduleConflictError
            event.start_at = new_start_at
            event.end_at = new_end_at
            await self._session.commit()
            await self._session.refresh(event)
            return CalendarWriteResult(event=event, reused=False)
        except (
            CalendarEventNotFoundError,
            CalendarScheduleConflictError,
            CalendarWriteConflictError,
        ):
            await self._session.rollback()
            raise
        except Exception:
            await self._session.rollback()
            raise

    async def cancel_event(
        self,
        *,
        user_id: UUID,
        event_id: UUID,
    ) -> CalendarWriteResult:
        """取消当前用户会议；重复取消返回同一个事件。"""

        try:
            event = await self._provider.get_by_id(user_id=user_id, event_id=event_id)
            if event is None:
                raise CalendarEventNotFoundError
            reused = event.status is CalendarEventStatus.CANCELLED
            event.status = CalendarEventStatus.CANCELLED
            await self._session.commit()
            await self._session.refresh(event)
            return CalendarWriteResult(event=event, reused=reused)
        except CalendarEventNotFoundError:
            await self._session.rollback()
            raise
        except Exception:
            await self._session.rollback()
            raise

    async def _event_exists(
        self,
        *,
        user_id: UUID,
        data: CalendarEventImport,
    ) -> bool:
        """通过外部 ID 和可选幂等键判断是否已经导入。"""

        event = await self._provider.get_by_external_id(
            user_id=user_id,
            provider=data.provider,
            external_id=data.external_id,
        )
        if event is not None:
            return True
        if data.idempotency_key is None:
            return False
        event = await self._provider.get_by_idempotency_key(
            user_id=user_id,
            idempotency_key=data.idempotency_key,
        )
        return event is not None

    @staticmethod
    def _matches_created_event(
        event: CalendarEvent,
        *,
        title: str,
        start_at: datetime,
        end_at: datetime,
        attendees: list[str],
        timezone: str,
    ) -> bool:
        return (
            event.title == title
            and event.start_at == start_at
            and event.end_at == end_at
            and event.attendees == attendees
            and event.timezone == timezone
        )

    @staticmethod
    def _validate_optional_window(
        *,
        window_start: datetime | None,
        window_end: datetime | None,
    ) -> None:
        """校验列表接口提供的可选时间边界。"""

        for value in (window_start, window_end):
            if value is not None and value.utcoffset() is None:
                raise InvalidCalendarRangeError
        if window_start is not None and window_end is not None and window_start >= window_end:
            raise InvalidCalendarRangeError

    @staticmethod
    def _validate_required_window(*, start_at: datetime, end_at: datetime) -> None:
        """校验冲突查询必须使用有效的带时区范围。"""

        if start_at.utcoffset() is None or end_at.utcoffset() is None or start_at >= end_at:
            raise InvalidCalendarRangeError
