"""企业日历导入、查询与可用性接口。"""

from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import request_id_context
from app.db.session import get_db_session
from app.models.user import User
from app.schemas.calendar import (
    CalendarAvailabilityResponse,
    CalendarEventImport,
    CalendarEventResponse,
)
from app.schemas.common import ApiResponse, Page
from app.security.dependencies import get_current_user
from app.services.calendar import CalendarService

router = APIRouter(prefix="/calendar", tags=["日历"])


@router.post(
    "/events/import",
    response_model=ApiResponse[CalendarEventResponse],
    status_code=status.HTTP_201_CREATED,
    summary="导入测试日历事件",
    responses={status.HTTP_409_CONFLICT: {"description": "事件已导入或发生数据冲突"}},
)
async def import_calendar_event(
    data: CalendarEventImport,
    current_user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> ApiResponse[CalendarEventResponse]:
    """导入当前用户已有的本地测试日程，不代表 Agent 创建会议。"""

    event = await CalendarService(session).import_event(user_id=current_user.id, data=data)
    return ApiResponse(
        success=True,
        data=CalendarEventResponse.model_validate(event),
        request_id=request_id_context.get(),
    )


@router.get(
    "/events",
    response_model=ApiResponse[Page[CalendarEventResponse]],
    summary="查询日历事件列表",
)
async def list_calendar_events(
    current_user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
    window_start: datetime | None = None,
    window_end: datetime | None = None,
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> ApiResponse[Page[CalendarEventResponse]]:
    """查询当前用户日程，可筛选与给定窗口相交的事件。"""

    page = await CalendarService(session).list_event_page(
        user_id=current_user.id,
        window_start=window_start,
        window_end=window_end,
        offset=offset,
        limit=limit,
    )
    return ApiResponse(
        success=True,
        data=Page(
            items=[CalendarEventResponse.model_validate(item) for item in page.items],
            total=page.total,
            offset=page.offset,
            limit=page.limit,
        ),
        request_id=request_id_context.get(),
    )


@router.get(
    "/availability",
    response_model=ApiResponse[CalendarAvailabilityResponse],
    summary="检查日历时间是否可用",
)
async def check_calendar_availability(
    start_at: datetime,
    end_at: datetime,
    current_user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
    exclude_event_id: UUID | None = None,
) -> ApiResponse[CalendarAvailabilityResponse]:
    """查询当前用户在给定范围内未取消的冲突事件。"""

    conflicts = await CalendarService(session).find_conflicts(
        user_id=current_user.id,
        start_at=start_at,
        end_at=end_at,
        exclude_event_id=exclude_event_id,
    )
    return ApiResponse(
        success=True,
        data=CalendarAvailabilityResponse(
            available=not conflicts,
            conflicts=[CalendarEventResponse.model_validate(item) for item in conflicts],
        ),
        request_id=request_id_context.get(),
    )


@router.get(
    "/events/{event_id}",
    response_model=ApiResponse[CalendarEventResponse],
    summary="查询日历事件详情",
    responses={status.HTTP_404_NOT_FOUND: {"description": "日历事件不存在"}},
)
async def get_calendar_event(
    event_id: UUID,
    current_user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> ApiResponse[CalendarEventResponse]:
    """返回当前用户自己的一条日历事件。"""

    event = await CalendarService(session).get_event(
        user_id=current_user.id,
        event_id=event_id,
    )
    return ApiResponse(
        success=True,
        data=CalendarEventResponse.model_validate(event),
        request_id=request_id_context.get(),
    )
