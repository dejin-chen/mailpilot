"""企业邮件导入与查询接口。"""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Depends, Query, Request, status
from langgraph.store.base import BaseStore
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.dependencies import get_agent_store, get_mail_processing_graph_factory
from app.core.logging import request_id_context
from app.db.session import AsyncSessionFactory, get_db_session
from app.models.user import User
from app.schemas.agent_run import AgentRunResponse, AgentRunStartResponse
from app.schemas.common import ApiResponse, Page
from app.schemas.email import (
    EmailImportResponse,
    EmailMessageImport,
    EmailMessageResponse,
    EmailThreadDetailResponse,
    EmailThreadResponse,
)
from app.security.dependencies import get_current_user
from app.services.email import EmailService
from app.services.mail_processing_workflow import (
    MailProcessingGraphFactory,
    MailProcessingWorkflowService,
    run_mail_processing_in_background,
)

router = APIRouter(prefix="/emails", tags=["邮件"])


@router.post(
    "/{thread_id}/process",
    response_model=ApiResponse[AgentRunStartResponse],
    status_code=status.HTTP_202_ACCEPTED,
    summary="启动邮件 Agent 处理",
)
async def process_email(
    thread_id: UUID,
    background_tasks: BackgroundTasks,
    request: Request,
    current_user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
    graph_factory: Annotated[
        MailProcessingGraphFactory,
        Depends(get_mail_processing_graph_factory),
    ],
    store: Annotated[BaseStore, Depends(get_agent_store)],
) -> ApiResponse[AgentRunStartResponse]:
    """先返回 pending AgentRun，再使用独立数据库 Session 在后台执行。"""

    service = MailProcessingWorkflowService(
        session,
        graph_factory,
        store,
    )
    run = await service.prepare(
        user_id=current_user.id,
        email_thread_id=thread_id,
        request_id=request_id_context.get(),
    )
    session_factory = getattr(
        request.app.state,
        "agent_background_session_factory",
        AsyncSessionFactory,
    )
    background_tasks.add_task(
        run_mail_processing_in_background,
        session_factory=session_factory,
        graph_factory=graph_factory,
        store=store,
        user_id=current_user.id,
        user_timezone=current_user.timezone,
        run_id=run.id,
        request_id=request_id_context.get(),
    )
    return ApiResponse(
        success=True,
        data=AgentRunStartResponse(
            run=AgentRunResponse.model_validate(run),
            approval_request_id=None,
        ),
        request_id=request_id_context.get(),
    )


@router.post(
    "/import",
    response_model=ApiResponse[EmailImportResponse],
    status_code=status.HTTP_201_CREATED,
    summary="导入测试邮件",
    responses={
        status.HTTP_401_UNAUTHORIZED: {"description": "访问令牌无效或缺失"},
        status.HTTP_409_CONFLICT: {"description": "邮件已导入或发生数据冲突"},
    },
)
async def import_email(
    data: EmailMessageImport,
    current_user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> ApiResponse[EmailImportResponse]:
    """把一封本地测试收件邮件导入当前登录用户的邮箱。"""

    imported = await EmailService(session).import_inbound_email(
        user_id=current_user.id,
        data=data,
    )
    return ApiResponse(
        success=True,
        data=EmailImportResponse(
            thread=EmailThreadResponse.model_validate(imported.thread),
            message=EmailMessageResponse.model_validate(imported.message),
            created_thread=imported.created_thread,
        ),
        request_id=request_id_context.get(),
    )


@router.get(
    "",
    response_model=ApiResponse[Page[EmailThreadResponse]],
    summary="查询邮件线程列表",
)
async def list_emails(
    current_user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> ApiResponse[Page[EmailThreadResponse]]:
    """按最新邮件时间倒序返回当前用户自己的线程。"""

    page = await EmailService(session).list_thread_page(
        user_id=current_user.id,
        offset=offset,
        limit=limit,
    )
    return ApiResponse(
        success=True,
        data=Page(
            items=[EmailThreadResponse.model_validate(item) for item in page.items],
            total=page.total,
            offset=page.offset,
            limit=page.limit,
        ),
        request_id=request_id_context.get(),
    )


@router.get(
    "/{thread_id}",
    response_model=ApiResponse[EmailThreadDetailResponse],
    summary="查询邮件线程详情",
    responses={status.HTTP_404_NOT_FOUND: {"description": "邮件线程不存在"}},
)
async def get_email_thread(
    thread_id: UUID,
    current_user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> ApiResponse[EmailThreadDetailResponse]:
    """返回当前用户线程及其中按时间排列的邮件。"""

    thread = await EmailService(session).get_thread(
        user_id=current_user.id,
        thread_id=thread_id,
    )
    return ApiResponse(
        success=True,
        data=EmailThreadDetailResponse.model_validate(thread),
        request_id=request_id_context.get(),
    )
