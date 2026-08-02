"""人工审批查询、决定和 LangGraph 恢复接口。"""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, status
from langgraph.checkpoint.base import BaseCheckpointSaver
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.dependencies import (
    get_agent_checkpointer,
    get_approved_action_executor,
    get_mail_processing_graph_factory,
)
from app.agent.nodes.execute_write import ApprovedActionExecutor
from app.core.logging import request_id_context
from app.db.session import get_db_session
from app.models.approval import ApprovalStatus
from app.models.user import User, UserRole
from app.schemas.approval import (
    ApprovalDecision,
    ApprovalDecisionResponse,
    ApprovalFeedbackRequest,
    ApprovalModifyRequest,
    ApprovalRejectRequest,
    ApprovalRequestResponse,
)
from app.schemas.common import ApiResponse, Page
from app.security.dependencies import get_current_user
from app.services.approval import ApprovalService
from app.services.approval_workflow import ApprovalWorkflowService
from app.services.mail_processing_workflow import MailProcessingGraphFactory

router = APIRouter(prefix="/approvals", tags=["人工审批"])


@router.get(
    "",
    response_model=ApiResponse[Page[ApprovalRequestResponse]],
    summary="查询审批列表",
)
async def list_approvals(
    current_user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
    approval_status: Annotated[ApprovalStatus | None, Query(alias="status")] = None,
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> ApiResponse[Page[ApprovalRequestResponse]]:
    """普通用户只看自己的审批，管理员可以查看全部用户审批。"""

    service = ApprovalService(session)
    if current_user.role is UserRole.ADMIN:
        page = await service.list_request_page_for_admin(
            status=approval_status,
            offset=offset,
            limit=limit,
        )
    else:
        page = await service.list_request_page(
            user_id=current_user.id,
            status=approval_status,
            offset=offset,
            limit=limit,
        )
    return ApiResponse(
        success=True,
        data=Page(
            items=[ApprovalRequestResponse.model_validate(item) for item in page.items],
            total=page.total,
            offset=page.offset,
            limit=page.limit,
        ),
        request_id=request_id_context.get(),
    )


@router.get(
    "/{approval_id}",
    response_model=ApiResponse[ApprovalRequestResponse],
    summary="查询审批详情",
    responses={status.HTTP_404_NOT_FOUND: {"description": "审批不存在或无权访问"}},
)
async def get_approval(
    approval_id: UUID,
    current_user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> ApiResponse[ApprovalRequestResponse]:
    """返回一张审批单；管理员只在读取接口拥有跨用户查看权限。"""

    service = ApprovalService(session)
    if current_user.role is UserRole.ADMIN:
        approval = await service.get_request_for_admin(approval_id=approval_id)
    else:
        approval = await service.get_request(
            user_id=current_user.id,
            approval_id=approval_id,
        )
    return ApiResponse(
        success=True,
        data=ApprovalRequestResponse.model_validate(approval),
        request_id=request_id_context.get(),
    )


@router.post(
    "/{approval_id}/approve",
    response_model=ApiResponse[ApprovalDecisionResponse],
    summary="接受审批",
)
async def approve(
    approval_id: UUID,
    current_user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
    checkpointer: Annotated[BaseCheckpointSaver, Depends(get_agent_checkpointer)],
    executor: Annotated[ApprovedActionExecutor, Depends(get_approved_action_executor)],
    graph_factory: Annotated[
        MailProcessingGraphFactory,
        Depends(get_mail_processing_graph_factory),
    ],
) -> ApiResponse[ApprovalDecisionResponse]:
    """保持原建议参数，接受当前审批并恢复同一 Graph。"""

    return await _decide_and_resume(
        approval_id=approval_id,
        current_user=current_user,
        session=session,
        checkpointer=checkpointer,
        executor=executor,
        graph_factory=graph_factory,
        decision=ApprovalDecision(status=ApprovalStatus.APPROVED),
    )


@router.post(
    "/{approval_id}/reject",
    response_model=ApiResponse[ApprovalDecisionResponse],
    summary="拒绝审批",
)
async def reject(
    approval_id: UUID,
    data: ApprovalRejectRequest,
    current_user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
    checkpointer: Annotated[BaseCheckpointSaver, Depends(get_agent_checkpointer)],
    executor: Annotated[ApprovedActionExecutor, Depends(get_approved_action_executor)],
    graph_factory: Annotated[
        MailProcessingGraphFactory,
        Depends(get_mail_processing_graph_factory),
    ],
) -> ApiResponse[ApprovalDecisionResponse]:
    """拒绝危险操作，可附带原因，并恢复 Graph 进入拒绝分支。"""

    return await _decide_and_resume(
        approval_id=approval_id,
        current_user=current_user,
        session=session,
        checkpointer=checkpointer,
        executor=executor,
        graph_factory=graph_factory,
        decision=ApprovalDecision(
            status=ApprovalStatus.REJECTED,
            feedback=data.feedback,
        ),
    )


@router.post(
    "/{approval_id}/approve-with-modifications",
    response_model=ApiResponse[ApprovalDecisionResponse],
    summary="修改参数后接受审批",
    responses={status.HTTP_422_UNPROCESSABLE_CONTENT: {"description": "修改参数无效"}},
)
async def approve_with_modifications(
    approval_id: UUID,
    data: ApprovalModifyRequest,
    current_user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
    checkpointer: Annotated[BaseCheckpointSaver, Depends(get_agent_checkpointer)],
    executor: Annotated[ApprovedActionExecutor, Depends(get_approved_action_executor)],
    graph_factory: Annotated[
        MailProcessingGraphFactory,
        Depends(get_mail_processing_graph_factory),
    ],
) -> ApiResponse[ApprovalDecisionResponse]:
    """按审批动作对应的精确 Schema 重新校验参数，保存后恢复 Graph。"""

    return await _decide_and_resume(
        approval_id=approval_id,
        current_user=current_user,
        session=session,
        checkpointer=checkpointer,
        executor=executor,
        graph_factory=graph_factory,
        decision=ApprovalDecision(
            status=ApprovalStatus.APPROVED,
            modified_arguments=data.modified_arguments,
        ),
    )


@router.post(
    "/{approval_id}/request-regeneration",
    response_model=ApiResponse[ApprovalDecisionResponse],
    summary="提供反馈并要求重新生成",
)
async def request_regeneration(
    approval_id: UUID,
    data: ApprovalFeedbackRequest,
    current_user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
    checkpointer: Annotated[BaseCheckpointSaver, Depends(get_agent_checkpointer)],
    executor: Annotated[ApprovedActionExecutor, Depends(get_approved_action_executor)],
    graph_factory: Annotated[
        MailProcessingGraphFactory,
        Depends(get_mail_processing_graph_factory),
    ],
) -> ApiResponse[ApprovalDecisionResponse]:
    """保存明确文字反馈并恢复 Graph；后续主流程会据此重新生成方案。"""

    return await _decide_and_resume(
        approval_id=approval_id,
        current_user=current_user,
        session=session,
        checkpointer=checkpointer,
        executor=executor,
        graph_factory=graph_factory,
        decision=ApprovalDecision(
            status=ApprovalStatus.FEEDBACK_REQUESTED,
            feedback=data.feedback,
        ),
    )


async def _decide_and_resume(
    *,
    approval_id: UUID,
    current_user: User,
    session: AsyncSession,
    checkpointer: BaseCheckpointSaver,
    executor: ApprovedActionExecutor,
    graph_factory: MailProcessingGraphFactory,
    decision: ApprovalDecision,
) -> ApiResponse[ApprovalDecisionResponse]:
    request_id = request_id_context.get()
    result = await ApprovalWorkflowService(
        session,
        checkpointer,
        executor,
        graph_factory,
    ).decide_and_resume(
        user_id=current_user.id,
        user_timezone=current_user.timezone,
        approval_id=approval_id,
        decision=decision,
        request_id=request_id,
    )
    return ApiResponse(
        success=True,
        data=ApprovalDecisionResponse(
            approval=ApprovalRequestResponse.model_validate(result.approval),
            graph_resumed=result.graph_resumed,
            already_resumed=result.already_resumed,
            next_approval_request_id=result.next_approval_request_id,
        ),
        request_id=request_id,
    )
