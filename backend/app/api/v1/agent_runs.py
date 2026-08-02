"""Agent 运行列表与状态查询接口。"""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Query, Request
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.sse import stream_agent_run_events
from app.core.logging import request_id_context
from app.db.session import AsyncSessionFactory, get_db_session
from app.models.agent_run import AgentRunStatus
from app.models.user import User
from app.schemas.agent_run import AgentRunResponse
from app.schemas.agent_run_event import (
    AgentRunEventListResponse,
    AgentRunEventResponse,
)
from app.schemas.common import ApiResponse, Page
from app.security.dependencies import get_current_user
from app.services.agent_run import AgentRunService
from app.services.agent_run_event import AgentRunEventService

router = APIRouter(prefix="/agent-runs", tags=["Agent 执行"])


@router.get(
    "",
    response_model=ApiResponse[Page[AgentRunResponse]],
    summary="查询 Agent 执行列表",
)
async def list_agent_runs(
    current_user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
    run_status: Annotated[AgentRunStatus | None, Query(alias="status")] = None,
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> ApiResponse[Page[AgentRunResponse]]:
    """只返回当前登录用户自己的执行记录。"""

    items, total = await AgentRunService(session).list_runs(
        user_id=current_user.id,
        status=run_status,
        offset=offset,
        limit=limit,
    )
    return ApiResponse(
        success=True,
        data=Page(
            items=[AgentRunResponse.model_validate(item) for item in items],
            total=total,
            offset=offset,
            limit=limit,
        ),
        request_id=request_id_context.get(),
    )


@router.get(
    "/{run_id}",
    response_model=ApiResponse[AgentRunResponse],
    summary="查询 Agent 执行状态",
)
async def get_agent_run(
    run_id: UUID,
    current_user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> ApiResponse[AgentRunResponse]:
    """按 user_id 隔离读取一次 AgentRun 的节点、结果、Token 和错误。"""

    run = await AgentRunService(session).get_run(
        user_id=current_user.id,
        run_id=run_id,
    )
    return ApiResponse(
        success=True,
        data=AgentRunResponse.model_validate(run),
        request_id=request_id_context.get(),
    )


@router.get(
    "/{run_id}/events/history",
    response_model=ApiResponse[AgentRunEventListResponse],
    summary="增量查询 Agent 执行轨迹",
)
async def list_agent_run_events(
    run_id: UUID,
    current_user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
    after: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
) -> ApiResponse[AgentRunEventListResponse]:
    """按严格递增 sequence 返回当前用户自己的执行事件。"""

    items, has_more = await AgentRunEventService(session).list_events(
        user_id=current_user.id,
        agent_run_id=run_id,
        after=after,
        limit=limit,
    )
    next_after = items[-1].sequence if items else after
    return ApiResponse(
        success=True,
        data=AgentRunEventListResponse(
            items=[AgentRunEventResponse.model_validate(item) for item in items],
            after=after,
            next_after=next_after,
            has_more=has_more,
        ),
        request_id=request_id_context.get(),
    )


@router.get(
    "/{run_id}/events",
    response_class=StreamingResponse,
    summary="订阅 Agent 执行事件 SSE",
)
async def subscribe_agent_run_events(
    run_id: UUID,
    request: Request,
    current_user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
    after: Annotated[int, Query(ge=0)] = 0,
    last_event_id: Annotated[str | None, Header(alias="Last-Event-ID")] = None,
) -> StreamingResponse:
    """鉴权并校验运行归属后，支持 query 或 Last-Event-ID 断线续传。"""

    await AgentRunService(session).get_run(user_id=current_user.id, run_id=run_id)
    cursor = _resume_cursor(after=after, last_event_id=last_event_id)
    session_factory = getattr(
        request.app.state,
        "agent_background_session_factory",
        AsyncSessionFactory,
    )
    return StreamingResponse(
        stream_agent_run_events(
            request=request,
            session_factory=session_factory,
            user_id=current_user.id,
            run_id=run_id,
            after=cursor,
        ),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


def _resume_cursor(*, after: int, last_event_id: str | None) -> int:
    """无效 Last-Event-ID 不覆盖已通过 Pydantic 校验的 after。"""

    if last_event_id is None:
        return after
    try:
        parsed = int(last_event_id)
    except ValueError:
        return after
    return max(after, parsed, 0)
