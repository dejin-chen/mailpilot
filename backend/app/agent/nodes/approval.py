"""审批单准备节点与 LangGraph interrupt 等待节点。"""

import logging
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from typing import Protocol
from uuid import UUID

from langgraph.runtime import Runtime
from langgraph.types import interrupt
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.approval_schemas import (
    ApprovalGateStatus,
    ApprovalInterruptPayload,
    ApprovalResumeSignal,
    WriteActionProposal,
)
from app.agent.approval_state import ApprovalGateState
from app.agent.context import AgentRuntimeContext
from app.core.exceptions import AppException
from app.db.session import AsyncSessionFactory
from app.models.approval import ApprovalAction, ApprovalStatus
from app.schemas.approval import ApprovalRequestCreate
from app.services.approval import ApprovalService

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class PreparedApproval:
    """节点只需要的审批记录快照，避免把 ORM 对象放进 Graph State。"""

    approval_request_id: UUID
    status: ApprovalStatus


class ApprovalRequestCreator(Protocol):
    """正式 Service 适配器和测试 Fake 共同遵循的最小接口。"""

    async def create(
        self,
        *,
        user_id: UUID,
        agent_run_id: UUID,
        proposal: WriteActionProposal,
        request_id: str,
    ) -> PreparedApproval: ...


SessionFactory = Callable[[], AbstractAsyncContextManager[AsyncSession]]


class ServiceApprovalRequestCreator:
    """通过 ApprovalService 幂等创建审批单。"""

    def __init__(self, session_factory: SessionFactory = AsyncSessionFactory) -> None:
        self._session_factory = session_factory

    async def create(
        self,
        *,
        user_id: UUID,
        agent_run_id: UUID,
        proposal: WriteActionProposal,
        request_id: str,
    ) -> PreparedApproval:
        idempotency_key = build_approval_idempotency_key(
            agent_run_id=agent_run_id,
            action=proposal.action,
            version=proposal.version,
        )
        async with self._session_factory() as session:
            approval = await ApprovalService(session).create_request(
                user_id=user_id,
                data=ApprovalRequestCreate(
                    agent_run_id=agent_run_id,
                    action=proposal.action,
                    proposed_arguments=proposal.arguments.model_dump(mode="json"),
                    idempotency_key=idempotency_key,
                    version=proposal.version,
                ),
                request_id=request_id,
            )
        return PreparedApproval(
            approval_request_id=approval.id,
            status=approval.status,
        )


def build_approval_idempotency_key(
    *,
    agent_run_id: UUID,
    action: ApprovalAction,
    version: int,
) -> str:
    """由可信后端生成稳定幂等键，不让模型或浏览器自行决定。"""

    return f"approval:{agent_run_id}:{action.value}:v{version}"


class PrepareApprovalNode:
    """先幂等落库审批单，再把安全快照写回 State。"""

    name = "prepare_approval"

    def __init__(self, creator: ApprovalRequestCreator) -> None:
        self._creator = creator

    async def __call__(
        self,
        state: ApprovalGateState,
        runtime: Runtime[AgentRuntimeContext],
    ) -> dict[str, object]:
        context = runtime.context
        if context is None:
            return _failure_update(
                node=self.name,
                code="AGENT_CONTEXT_MISSING",
                message="Agent 运行时身份缺失",
            )
        try:
            proposal = WriteActionProposal.model_validate(state["proposal"])
            approval = await self._creator.create(
                user_id=context.user_id,
                agent_run_id=context.agent_run_id,
                proposal=proposal,
                request_id=context.request_id,
            )
        except AppException as exc:
            return _failure_update(
                node=self.name,
                code=exc.code,
                message=exc.message,
            )
        except Exception as exc:
            logger.exception(
                "准备人工审批失败",
                extra={"node": self.name, "error_type": type(exc).__name__},
            )
            return _failure_update(
                node=self.name,
                code="APPROVAL_PREPARATION_ERROR",
                message="创建人工审批请求失败",
            )

        return {
            "approval_request_id": approval.approval_request_id,
            "approval_status": approval.status,
            "gate_status": ApprovalGateStatus.WAITING_APPROVAL,
            "current_node": self.name,
        }


class WaitForApprovalNode:
    """把 JSON 安全载荷交给调用方，并暂停到 Command(resume=...) 到来。"""

    name = "wait_for_approval"

    def __call__(
        self,
        state: ApprovalGateState,
        runtime: Runtime[AgentRuntimeContext],
    ) -> dict[str, object]:
        context = runtime.context
        if context is None:
            return _failure_update(
                node=self.name,
                code="AGENT_CONTEXT_MISSING",
                message="Agent 运行时身份缺失",
            )
        proposal = WriteActionProposal.model_validate(state["proposal"])
        approval_request_id = state["approval_request_id"]
        payload = ApprovalInterruptPayload(
            approval_request_id=approval_request_id,
            agent_run_id=context.agent_run_id,
            action=proposal.action,
            proposed_arguments=proposal.arguments.model_dump(mode="json"),
            summary=proposal.summary,
            version=proposal.version,
        )
        resumed = interrupt(payload.model_dump(mode="json"))
        signal = ApprovalResumeSignal.model_validate(resumed)
        if signal.approval_request_id != approval_request_id:
            msg = "恢复信号与当前审批单不匹配"
            raise ValueError(msg)
        return {
            "approval_status": signal.status,
            "gate_status": ApprovalGateStatus.RESUMED,
            "current_node": self.name,
            "resume_signal": signal,
            **({"regeneration_feedback": signal.feedback} if signal.feedback is not None else {}),
        }


def _failure_update(*, node: str, code: str, message: str) -> dict[str, object]:
    return {
        "gate_status": ApprovalGateStatus.FAILED,
        "current_node": node,
        "error_code": code,
        "error_message": message,
    }
