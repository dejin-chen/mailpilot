"""当前用户长期记忆的查询、创建、修改和删除接口。"""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import request_id_context
from app.db.session import get_db_session
from app.models.memory import MemoryType
from app.models.user import User
from app.schemas.common import ApiResponse, Page
from app.schemas.memory import (
    MemoryDeleteResponse,
    MemoryProfileCreate,
    MemoryProfileResponse,
    MemoryProfileUpdate,
    MemoryProfileVersionResponse,
)
from app.security.dependencies import get_current_user
from app.services.memory import MemoryService

router = APIRouter(prefix="/memories", tags=["长期记忆"])


@router.post(
    "",
    response_model=ApiResponse[MemoryProfileResponse],
    status_code=status.HTTP_201_CREATED,
    summary="创建长期记忆",
    responses={
        status.HTTP_409_CONFLICT: {"description": "相同类型和 key 的记忆已经存在"},
        status.HTTP_422_UNPROCESSABLE_CONTENT: {"description": "记忆内容无效"},
    },
)
async def create_memory(
    data: MemoryProfileCreate,
    current_user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> ApiResponse[MemoryProfileResponse]:
    """用户明确提交偏好时，创建第一个可追溯版本。"""

    record = await MemoryService(session).create_profile(
        user_id=current_user.id,
        actor_user_id=current_user.id,
        data=data,
        request_id=request_id_context.get(),
    )
    return ApiResponse(
        success=True,
        data=MemoryProfileResponse.model_validate(record),
        request_id=request_id_context.get(),
    )


@router.get(
    "",
    response_model=ApiResponse[Page[MemoryProfileResponse]],
    summary="查询长期记忆列表",
)
async def list_memories(
    current_user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
    memory_type: MemoryType | None = None,
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> ApiResponse[Page[MemoryProfileResponse]]:
    """只返回当前登录用户自己的最新记忆版本。"""

    page = await MemoryService(session).list_profile_page(
        user_id=current_user.id,
        actor_user_id=current_user.id,
        memory_type=memory_type,
        offset=offset,
        limit=limit,
        request_id=request_id_context.get(),
    )
    return ApiResponse(
        success=True,
        data=Page(
            items=[MemoryProfileResponse.model_validate(item) for item in page.items],
            total=page.total,
            offset=page.offset,
            limit=page.limit,
        ),
        request_id=request_id_context.get(),
    )


@router.get(
    "/{memory_id}/versions",
    response_model=ApiResponse[Page[MemoryProfileVersionResponse]],
    summary="查询长期记忆版本历史",
    responses={status.HTTP_404_NOT_FOUND: {"description": "记忆不存在或无权访问"}},
)
async def list_memory_versions(
    memory_id: UUID,
    current_user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> ApiResponse[Page[MemoryProfileVersionResponse]]:
    """按版本号倒序返回当前用户自己的不可变记忆历史。"""

    page = await MemoryService(session).list_version_page(
        user_id=current_user.id,
        actor_user_id=current_user.id,
        memory_id=memory_id,
        offset=offset,
        limit=limit,
        request_id=request_id_context.get(),
    )
    return ApiResponse(
        success=True,
        data=Page(
            items=[MemoryProfileVersionResponse.model_validate(item) for item in page.items],
            total=page.total,
            offset=page.offset,
            limit=page.limit,
        ),
        request_id=request_id_context.get(),
    )


@router.get(
    "/{memory_id}",
    response_model=ApiResponse[MemoryProfileResponse],
    summary="查询长期记忆详情",
    responses={status.HTTP_404_NOT_FOUND: {"description": "记忆不存在或无权访问"}},
)
async def get_memory(
    memory_id: UUID,
    current_user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> ApiResponse[MemoryProfileResponse]:
    """读取一项当前记忆，访问本身也会写入审计日志。"""

    record = await MemoryService(session).get_profile(
        user_id=current_user.id,
        actor_user_id=current_user.id,
        memory_id=memory_id,
        request_id=request_id_context.get(),
    )
    return ApiResponse(
        success=True,
        data=MemoryProfileResponse.model_validate(record),
        request_id=request_id_context.get(),
    )


@router.put(
    "/{memory_id}",
    response_model=ApiResponse[MemoryProfileResponse],
    summary="修改长期记忆",
    responses={
        status.HTTP_404_NOT_FOUND: {"description": "记忆不存在或无权访问"},
        status.HTTP_409_CONFLICT: {"description": "版本已过期"},
        status.HTTP_422_UNPROCESSABLE_CONTENT: {"description": "记忆内容无效"},
    },
)
async def update_memory(
    memory_id: UUID,
    data: MemoryProfileUpdate,
    current_user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> ApiResponse[MemoryProfileResponse]:
    """基于 expected_version 追加新版本，防止覆盖别人刚完成的修改。"""

    record = await MemoryService(session).update_profile(
        user_id=current_user.id,
        actor_user_id=current_user.id,
        memory_id=memory_id,
        data=data,
        request_id=request_id_context.get(),
    )
    return ApiResponse(
        success=True,
        data=MemoryProfileResponse.model_validate(record),
        request_id=request_id_context.get(),
    )


@router.delete(
    "/{memory_id}",
    response_model=ApiResponse[MemoryDeleteResponse],
    summary="删除长期记忆",
    responses={status.HTTP_404_NOT_FOUND: {"description": "记忆不存在或无权访问"}},
)
async def delete_memory(
    memory_id: UUID,
    current_user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> ApiResponse[MemoryDeleteResponse]:
    """删除当前用户的一项记忆及其版本正文，保留最小审计元数据。"""

    await MemoryService(session).delete_profile(
        user_id=current_user.id,
        actor_user_id=current_user.id,
        memory_id=memory_id,
        request_id=request_id_context.get(),
    )
    return ApiResponse(
        success=True,
        data=MemoryDeleteResponse(memory_id=memory_id),
        request_id=request_id_context.get(),
    )
