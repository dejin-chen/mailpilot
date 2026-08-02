"""从明确审批反馈中安全提取并持久化长期记忆 Patch。"""

import logging
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from typing import Protocol
from uuid import UUID

from langgraph.runtime import Runtime
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.context import AgentRuntimeContext
from app.agent.nodes.common import NodeUpdate, failure_update, llm_failure_update
from app.agent.prompts import build_memory_feedback_messages
from app.agent.schemas import AgentRunStatus
from app.agent.state import MailAgentState
from app.core.exceptions import AppException
from app.db.session import AsyncSessionFactory
from app.integrations.llm.client import StructuredLlmClient
from app.models.memory import MemoryType
from app.schemas.memory_feedback import (
    ContactMemoryUpdateProposal,
    FeedbackMemoryDecision,
    MemoryFeedbackUpdateResult,
    MemoryUpdateProposal,
)
from app.services.memory import MemoryService

logger = logging.getLogger(__name__)

LONG_TERM_MEMORY_MARKERS = (
    "以后",
    "今后",
    "从现在起",
    "之后都",
    "每次",
    "默认",
    "一直",
    "长期",
    "固定",
    "请记住",
    "记住这个偏好",
)


def has_explicit_long_term_memory_signal(feedback: str) -> bool:
    """用保守规则拦住只针对当前方案的一次性修改。"""

    normalized = feedback.strip().casefold()
    return bool(normalized) and any(
        marker.casefold() in normalized for marker in LONG_TERM_MEMORY_MARKERS
    )


class MemoryFeedbackUpdater(Protocol):
    """正式 Service 适配器和测试 Fake 共同遵循的最小接口。"""

    async def apply(
        self,
        *,
        user_id: UUID,
        approval_request_id: UUID,
        proposal: MemoryUpdateProposal,
        request_id: str,
    ) -> MemoryFeedbackUpdateResult: ...


SessionFactory = Callable[[], AbstractAsyncContextManager[AsyncSession]]


class ServiceMemoryFeedbackUpdater:
    """使用独立数据库 Session 执行记忆 Patch 的正式适配器。"""

    def __init__(self, session_factory: SessionFactory = AsyncSessionFactory) -> None:
        self._session_factory = session_factory

    async def apply(
        self,
        *,
        user_id: UUID,
        approval_request_id: UUID,
        proposal: MemoryUpdateProposal,
        request_id: str,
    ) -> MemoryFeedbackUpdateResult:
        async with self._session_factory() as session:
            return await MemoryService(session).apply_feedback_patch(
                user_id=user_id,
                actor_user_id=user_id,
                approval_request_id=approval_request_id,
                proposal=proposal,
                request_id=request_id,
            )


class ApplyApprovalFeedbackNode:
    """重生成当前方案，并只在明确长期指令下尝试更新记忆。"""

    name = "apply_approval_feedback"

    def __init__(
        self,
        llm_client: StructuredLlmClient,
        updater: MemoryFeedbackUpdater,
    ) -> None:
        self._llm_client = llm_client
        self._updater = updater

    async def __call__(
        self,
        state: MailAgentState,
        runtime: Runtime[AgentRuntimeContext],
    ) -> NodeUpdate:
        feedback = state.get("regeneration_feedback", "").strip()
        if not feedback:
            return failure_update(
                node=self.name,
                code="APPROVAL_FEEDBACK_MISSING",
                message="重新生成方案时缺少用户反馈",
            )
        count = state.get("regeneration_count", 0)
        if count >= state.get("max_regenerations", 2):
            return failure_update(
                node=self.name,
                code="AGENT_REGENERATION_LIMIT_EXCEEDED",
                message="方案重新生成次数已达到本次工作流上限",
            )

        base_update: NodeUpdate = {
            "proposal_version": state.get("proposal_version", 1) + 1,
            "regeneration_count": count + 1,
            "current_node": self.name,
            "run_status": AgentRunStatus.RUNNING,
        }
        if not has_explicit_long_term_memory_signal(feedback):
            return base_update

        context = runtime.context
        approval_request_id = state.get("approval_request_id")
        sender = state.get("sender", "").strip().lower()
        if context is None or approval_request_id is None or not sender:
            return failure_update(
                node=self.name,
                code="AGENT_STATE_DATA_MISSING",
                message="长期记忆更新缺少用户、审批单或发件人信息",
            )

        try:
            result = await self._llm_client.ainvoke_structured(
                operation="analyze_memory_feedback",
                messages=build_memory_feedback_messages(state),
                schema=FeedbackMemoryDecision,
            )
        except Exception as exc:
            return llm_failure_update(node=self.name, exc=exc, logger=logger)

        decision = result.parsed
        base_update["memory_feedback_decision"] = decision
        base_update["model_usages"] = [result.usage]
        if not decision.should_update or decision.proposal is None:
            return base_update

        validation_error = self._validate_proposal(
            feedback=feedback,
            sender=sender,
            proposal=decision.proposal,
        )
        if validation_error is not None:
            failed = failure_update(
                node=self.name,
                code="MEMORY_FEEDBACK_POLICY_VIOLATION",
                message=validation_error,
            )
            failed["model_usages"] = [result.usage]
            failed["memory_feedback_decision"] = decision
            return failed

        try:
            memory_update = await self._updater.apply(
                user_id=context.user_id,
                approval_request_id=approval_request_id,
                proposal=decision.proposal,
                request_id=context.request_id,
            )
        except AppException as exc:
            failed = failure_update(
                node=self.name,
                code=exc.code,
                message=exc.message,
            )
            failed["model_usages"] = [result.usage]
            failed["memory_feedback_decision"] = decision
            return failed
        except Exception as exc:
            logger.exception(
                "审批反馈写入长期记忆失败",
                extra={"node": self.name, "error_type": type(exc).__name__},
            )
            failed = failure_update(
                node=self.name,
                code="MEMORY_FEEDBACK_UPDATE_ERROR",
                message="审批反馈未能写入长期记忆",
            )
            failed["model_usages"] = [result.usage]
            failed["memory_feedback_decision"] = decision
            return failed

        base_update["memory_update"] = memory_update
        return base_update

    @staticmethod
    def _validate_proposal(
        *,
        feedback: str,
        sender: str,
        proposal: MemoryUpdateProposal,
    ) -> str | None:
        """在 LLM Schema 之外校验证据来源和联系人边界。"""

        if proposal.evidence not in feedback:
            return "长期记忆建议的 evidence 必须逐字来自用户反馈"
        if proposal.memory_type is MemoryType.CONTACT and (
            not isinstance(proposal, ContactMemoryUpdateProposal)
            or str(proposal.memory_key).lower() != sender
        ):
            return "联系人记忆只能更新当前邮件发件人"
        return None
