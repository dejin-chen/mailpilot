"""把邮件分析结果转换为可审批的邮件或日历写操作方案。"""

import logging
from collections.abc import Callable
from typing import Any, Protocol

from langgraph.runtime import Runtime

from app.agent.approval_schemas import WriteActionProposal
from app.agent.context import AgentRuntimeContext
from app.agent.nodes.common import NodeUpdate, failure_update, llm_failure_update
from app.agent.prompts import (
    build_calendar_regeneration_messages,
    build_draft_messages,
)
from app.agent.schemas import (
    AgentRunStatus,
    DraftPurpose,
    EmailAction,
    EmailClassification,
    EmailDraft,
    EmailDraftContent,
    ExtractedIntent,
)
from app.agent.security import validate_draft_policy
from app.agent.state import MailAgentState
from app.core.exceptions import AppException
from app.integrations.llm.client import StructuredLlmClient
from app.integrations.mcp.results import (
    extract_mcp_business_error,
    normalize_mcp_output,
)
from app.mcp.schemas import EmailDraftResult
from app.models.approval import ApprovalAction
from app.schemas.approval import (
    CreateEventArguments,
    SendEmailArguments,
)

logger = logging.getLogger(__name__)


class SafeToolClient(Protocol):
    """草稿节点只依赖一个受白名单约束的 MCP 调用接口。"""

    async def invoke_tool(self, tool_name: str, arguments: dict[str, Any]) -> Any: ...


SafeToolClientFactory = Callable[[AgentRuntimeContext], SafeToolClient]


def build_draft_idempotency_key(*, agent_run_id: object, version: int) -> str:
    """同一运行的同一草稿版本始终使用相同幂等键。"""

    return f"draft:{agent_run_id}:v{version}"


class GenerateDraftNode:
    """模型只写正文，Python 确定用途、收件人、抄送和回复主题。"""

    name = "generate_draft"

    def __init__(self, llm_client: StructuredLlmClient) -> None:
        self._llm_client = llm_client

    async def __call__(self, state: MailAgentState) -> NodeUpdate:
        try:
            result = await self._llm_client.ainvoke_structured(
                operation=self.name,
                messages=build_draft_messages(state),
                schema=EmailDraftContent,
            )
        except Exception as exc:
            return llm_failure_update(node=self.name, exc=exc, logger=logger)

        classification = state.get("classification")
        intent = state.get("intent")
        if not isinstance(classification, EmailClassification) or not isinstance(
            intent, ExtractedIntent
        ):
            return failure_update(
                node=self.name,
                code="AGENT_STATE_DATA_MISSING",
                message="草稿安全校验前缺少结构化分类或意图",
            )
        purpose = DraftPurpose.REPLY
        if intent.needs_clarification:
            purpose = DraftPurpose.CLARIFICATION
        elif classification.action is EmailAction.REMIND:
            purpose = DraftPurpose.REMINDER
        original_subject = str(state.get("email_subject", "")).strip()
        subject = (
            original_subject
            if original_subject.lower().startswith("re:")
            else f"Re: {original_subject}"
        )
        draft = EmailDraft(
            purpose=purpose,
            recipients=[state.get("sender", "")],
            cc=[],
            subject=subject,
            body_text=result.parsed.body_text,
        )
        violation = validate_draft_policy(
            draft=draft,
            sender=state.get("sender", ""),
            classification=classification,
            intent=intent,
        )
        if violation is not None:
            return failure_update(
                node=self.name,
                code=violation.code,
                message=violation.message,
            )

        return {
            "draft": draft,
            "model_usages": [result.usage],
            "current_node": self.name,
            "run_status": AgentRunStatus.RUNNING,
        }


class CreateDraftProposalNode:
    """通过 MCP 幂等保存本地草稿，再生成只引用草稿 ID 的发送审批方案。"""

    name = "create_draft_proposal"

    def __init__(self, client_factory: SafeToolClientFactory) -> None:
        self._client_factory = client_factory

    async def __call__(
        self,
        state: MailAgentState,
        runtime: Runtime[AgentRuntimeContext],
    ) -> NodeUpdate:
        context = runtime.context
        draft = state.get("draft")
        if context is None:
            return failure_update(
                node=self.name,
                code="AGENT_CONTEXT_MISSING",
                message="创建草稿缺少可信运行上下文",
            )
        if not isinstance(draft, EmailDraft):
            return failure_update(
                node=self.name,
                code="AGENT_STATE_DATA_MISSING",
                message="创建草稿前缺少结构化草稿内容",
            )

        call_count = state.get("tool_call_count", 0)
        if call_count >= state.get("max_tool_calls", 4):
            return failure_update(
                node=self.name,
                code="TOOL_CALL_LIMIT_EXCEEDED",
                message="工具调用次数已达到本次工作流上限",
            )

        version = state.get("proposal_version", 1)
        idempotency_key = build_draft_idempotency_key(
            agent_run_id=context.agent_run_id,
            version=version,
        )
        arguments = {
            "thread_id": str(state["email_thread_id"]),
            "recipients": [str(address) for address in draft.recipients],
            "cc": [str(address) for address in draft.cc],
            "subject": draft.subject,
            "body_text": draft.body_text,
            "idempotency_key": idempotency_key,
        }
        try:
            client = self._client_factory(context)
            raw_output = await client.invoke_tool("create_email_draft", arguments)
            output = normalize_mcp_output(raw_output)
        except AppException as exc:
            return failure_update(node=self.name, code=exc.code, message=exc.message)
        except Exception as exc:
            logger.exception(
                "通过 MCP 创建邮件草稿失败",
                extra={"node": self.name, "error_type": type(exc).__name__},
            )
            return failure_update(
                node=self.name,
                code="MCP_DRAFT_CREATION_ERROR",
                message="通过 MCP 创建邮件草稿失败",
                retryable=True,
            )

        business_error = extract_mcp_business_error(output)
        if business_error is not None:
            code, message = business_error
            return failure_update(node=self.name, code=code, message=message)
        try:
            created = EmailDraftResult.model_validate(output)
        except Exception as exc:
            logger.warning(
                "MCP 草稿结果结构无效",
                extra={"node": self.name, "error_type": type(exc).__name__},
            )
            return failure_update(
                node=self.name,
                code="MCP_DRAFT_RESULT_INVALID",
                message="MCP 草稿结果结构无效",
            )

        proposal = WriteActionProposal(
            action=ApprovalAction.SEND_EMAIL,
            arguments=SendEmailArguments(draft_message_id=created.message.id),
            summary=f"发送邮件草稿：{draft.subject or state.get('email_subject', '无主题')}",
            version=version,
        )
        return {
            "draft_message_id": created.message.id,
            "proposal": proposal,
            "tool_call_count": call_count + 1,
            "current_node": self.name,
            "run_status": AgentRunStatus.RUNNING,
        }


class BuildCreateEventProposalNode:
    """从已校验且确认无冲突的会议意图构造创建会议审批方案。"""

    name = "build_create_event_proposal"

    def __call__(self, state: MailAgentState) -> NodeUpdate:
        intent = state.get("intent")
        if not isinstance(intent, ExtractedIntent):
            return failure_update(
                node=self.name,
                code="AGENT_STATE_DATA_MISSING",
                message="创建会议方案前缺少结构化意图",
            )
        meeting = intent.meeting
        if (
            not meeting.detected
            or not meeting.time_information_complete
            or meeting.start_at is None
            or meeting.end_at is None
            or meeting.timezone is None
        ):
            return failure_update(
                node=self.name,
                code="MEETING_INTENT_INCOMPLETE",
                message="会议时间信息不完整，不能生成创建会议方案",
            )

        version = state.get("proposal_version", 1)
        arguments = CreateEventArguments(
            title=meeting.title or state.get("email_subject", "邮件会议"),
            start_at=meeting.start_at,
            end_at=meeting.end_at,
            attendees=meeting.attendees,
            timezone=meeting.timezone,
        )
        return {
            "proposal": WriteActionProposal(
                action=ApprovalAction.CREATE_EVENT,
                arguments=arguments,
                summary=f"创建会议：{arguments.title}",
                version=version,
            ),
            "current_node": self.name,
            "run_status": AgentRunStatus.RUNNING,
        }


class RegenerateCreateEventProposalNode:
    """根据用户反馈重写会议标题或参会人，但不绕过日历时间检查。"""

    name = "regenerate_create_event_proposal"

    def __init__(self, llm_client: StructuredLlmClient) -> None:
        self._llm_client = llm_client

    async def __call__(self, state: MailAgentState) -> NodeUpdate:
        proposal = state.get("proposal")
        if (
            not isinstance(proposal, WriteActionProposal)
            or proposal.action is not ApprovalAction.CREATE_EVENT
            or not isinstance(proposal.arguments, CreateEventArguments)
        ):
            return failure_update(
                node=self.name,
                code="AGENT_STATE_DATA_MISSING",
                message="重新生成会议方案前缺少原始创建会议方案",
            )
        original = proposal.arguments
        try:
            result = await self._llm_client.ainvoke_structured(
                operation=self.name,
                messages=build_calendar_regeneration_messages(state),
                schema=CreateEventArguments,
            )
        except Exception as exc:
            return llm_failure_update(node=self.name, exc=exc, logger=logger)

        regenerated = result.parsed
        if (
            regenerated.start_at != original.start_at
            or regenerated.end_at != original.end_at
            or regenerated.timezone != original.timezone
        ):
            return failure_update(
                node=self.name,
                code="CALENDAR_REGENERATION_POLICY_VIOLATION",
                message="文字反馈不能在未重新检查日历时改变会议时间或时区",
            )
        return {
            "proposal": WriteActionProposal(
                action=ApprovalAction.CREATE_EVENT,
                arguments=regenerated,
                summary=f"根据反馈更新会议方案：{regenerated.title}",
                version=state.get("proposal_version", 1),
            ),
            "model_usages": [result.usage],
            "current_node": self.name,
            "run_status": AgentRunStatus.RUNNING,
        }
