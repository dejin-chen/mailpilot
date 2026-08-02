"""管理员审计日志查询接口。"""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import request_id_context
from app.db.session import get_db_session
from app.models.user import User
from app.schemas.audit import AuditLogResponse
from app.schemas.common import ApiResponse, Page
from app.security.dependencies import get_current_admin
from app.services.audit import AuditService

router = APIRouter(prefix="/audit-logs", tags=["审计日志"])


@router.get(
    "",
    response_model=ApiResponse[Page[AuditLogResponse]],
    summary="管理员查询审计日志",
)
async def list_audit_logs(
    _: Annotated[User, Depends(get_current_admin)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
    user_id: Annotated[UUID | None, Query(description="按数据所有者筛选")] = None,
    action: Annotated[
        str | None,
        Query(min_length=1, max_length=100, description="按精确动作名称筛选"),
    ] = None,
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> ApiResponse[Page[AuditLogResponse]]:
    """只有管理员可读取；普通用户不能借此查看其他用户活动。"""

    page = await AuditService(session).list_page_for_admin(
        user_id=user_id,
        action=action,
        offset=offset,
        limit=limit,
    )
    return ApiResponse(
        success=True,
        data=Page(
            items=[AuditLogResponse.model_validate(item) for item in page.items],
            total=page.total,
            offset=page.offset,
            limit=page.limit,
        ),
        request_id=request_id_context.get(),
    )
