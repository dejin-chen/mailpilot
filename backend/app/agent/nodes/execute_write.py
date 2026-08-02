"""人工审批通过后执行一个允许列表中的 MCP 写工具。"""

import logging
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from typing import Protocol
from uuid import UUID

from langgraph.runtime import Runtime
from pydantic import JsonValue
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.approval_schemas import (
    WriteActionExecution,
    WriteExecutionStatus,
)
from app.agent.approval_state import ApprovalGateState
from app.agent.context import AgentRuntimeContext
from app.agent.exceptions import ApprovedActionExecutionError
from app.core.config import Settings, get_settings
from app.core.exceptions import AppException
from app.db.session import AsyncSessionFactory
from app.integrations.mcp.client import APPROVAL_REQUIRED_TOOLS, MailPilotMcpClient
from app.integrations.mcp.exceptions import McpToolInvocationError
from app.integrations.mcp.results import (
    extract_mcp_business_error,
    normalize_mcp_output,
)
from app.models.approval import ApprovalRequest, ApprovalStatus
from app.repositories.approval import ApprovalRepository
from app.schemas.approval import WRITE_ACTION_ARGUMENT_MODELS
from app.services.approved_tool_execution import (
    ApprovedToolExecutionService,
    build_write_idempotency_key,
)
from app.services.exceptions import ApprovalExecutionNotAllowedError

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class ExecutedApprovedAction:
    """节点与正式执行器之间传递的最小 JSON 安全结果。"""

    execution: WriteActionExecution


class ApprovedActionExecutor(Protocol):
    """正式 MCP 执行器和测试 Fake 共同遵循的接口。"""

    async def execute(
        self,
        *,
        user_id: UUID,
        agent_run_id: UUID,
        approval_request_id: UUID,
        request_id: str,
    ) -> ExecutedApprovedAction: ...


SessionFactory = Callable[[], AbstractAsyncContextManager[AsyncSession]]


class ServiceApprovedActionExecutor:
    """从数据库读取最终审批参数，再通过白名单 MCP Client 执行。"""

    def __init__(
        self,
        *,
        settings: Settings | None = None,
        session_factory: SessionFactory = AsyncSessionFactory,
    ) -> None:
        self._settings = settings or get_settings()
        self._session_factory = session_factory

    async def execute(
        self,
        *,
        user_id: UUID,
        agent_run_id: UUID,
        approval_request_id: UUID,
        request_id: str,
    ) -> ExecutedApprovedAction:
        approval = await self._load_approval(
            user_id=user_id,
            agent_run_id=agent_run_id,
            approval_request_id=approval_request_id,
        )
        argument_model = WRITE_ACTION_ARGUMENT_MODELS[approval.action]
        parsed_arguments = argument_model.model_validate(
            approval.modified_arguments or approval.proposed_arguments
        )
        arguments = parsed_arguments.model_dump(mode="json")
        idempotency_key = build_write_idempotency_key(
            approval_id=approval.id,
            action=approval.action,
            version=approval.version,
        )
        tool_name = approval.action.value
        tool_arguments = {
            **arguments,
            "approval_id": str(approval.id),
            "idempotency_key": idempotency_key,
        }
        client = MailPilotMcpClient(
            settings=self._settings,
            user_id=user_id,
            request_id=request_id,
            allowed_tools=frozenset({tool_name}) & APPROVAL_REQUIRED_TOOLS,
        )
        try:
            raw_output = await client.invoke_tool(tool_name, tool_arguments)
        except McpToolInvocationError as exc:
            reconciled = await self._reconcile_after_transport_error(
                user_id=user_id,
                approval_request_id=approval.id,
            )
            if reconciled is not None:
                return ExecutedApprovedAction(
                    execution=WriteActionExecution(
                        action=approval.action,
                        tool_name=tool_name,
                        idempotency_key=idempotency_key,
                        arguments=arguments,
                        result=reconciled,
                    )
                )
            await self._mark_uncertain_if_started(
                user_id=user_id,
                approval_request_id=approval.id,
                idempotency_key=idempotency_key,
                error_code=exc.code,
                error_message=exc.message,
                request_id=request_id,
            )
            raise ApprovedActionExecutionError(
                code=exc.code,
                message="写工具调用结果暂时无法确定，禁止自动重试",
                uncertain=True,
            ) from exc

        output = normalize_mcp_output(raw_output)
        business_error = extract_mcp_business_error(output)
        if business_error is not None:
            code, message = business_error
            raise ApprovedActionExecutionError(code=code, message=message)
        if not isinstance(output, dict):
            raise ApprovedActionExecutionError(
                code="MCP_WRITE_RESULT_INVALID",
                message="写工具没有返回结构化结果",
                uncertain=True,
            )
        return ExecutedApprovedAction(
            execution=WriteActionExecution(
                action=approval.action,
                tool_name=tool_name,
                idempotency_key=idempotency_key,
                arguments=arguments,
                result=output,
            )
        )

    async def _load_approval(
        self,
        *,
        user_id: UUID,
        agent_run_id: UUID,
        approval_request_id: UUID,
    ) -> ApprovalRequest:
        async with self._session_factory() as session:
            approval = await ApprovalRepository(session).get_by_id(
                user_id=user_id,
                approval_id=approval_request_id,
            )
            if (
                approval is None
                or approval.agent_run_id != agent_run_id
                or approval.status
                not in {
                    ApprovalStatus.APPROVED,
                    ApprovalStatus.EXECUTING,
                    ApprovalStatus.EXECUTED,
                }
            ):
                raise ApprovalExecutionNotAllowedError
            return approval

    async def _reconcile_after_transport_error(
        self,
        *,
        user_id: UUID,
        approval_request_id: UUID,
    ) -> dict[str, JsonValue] | None:
        """连接失败后只读取服务端事实，不再次调用有副作用工具。"""

        async with self._session_factory() as session:
            approval = await ApprovalRepository(session).get_by_id(
                user_id=user_id,
                approval_id=approval_request_id,
            )
            if (
                approval is not None
                and approval.status is ApprovalStatus.EXECUTED
                and approval.execution_result is not None
            ):
                return approval.execution_result
            return None

    async def _mark_uncertain_if_started(
        self,
        *,
        user_id: UUID,
        approval_request_id: UUID,
        idempotency_key: str,
        error_code: str,
        error_message: str,
        request_id: str,
    ) -> None:
        """只有 MCP Server 已登记执行意图时才有 ToolCallLog 可标记。"""

        try:
            async with self._session_factory() as session:
                await ApprovedToolExecutionService(session).mark_uncertain(
                    user_id=user_id,
                    approval_id=approval_request_id,
                    idempotency_key=idempotency_key,
                    error_code=error_code,
                    error_message=error_message,
                    request_id=request_id,
                )
        except Exception as exc:
            # 请求可能在到达 MCP Server 前就失败，此时没有工具日志可更新。
            logger.warning(
                "无法把写工具调用标记为结果未知",
                extra={
                    "approval_request_id": str(approval_request_id),
                    "error_type": type(exc).__name__,
                },
            )
            return


class ExecuteApprovedActionNode:
    """执行一个已审批写操作；任何失败都明确结束，不形成自由循环。"""

    name = "execute_approved_action"

    def __init__(self, executor: ApprovedActionExecutor) -> None:
        self._executor = executor

    async def __call__(
        self,
        state: ApprovalGateState,
        runtime: Runtime[AgentRuntimeContext],
    ) -> dict[str, object]:
        context = runtime.context
        approval_request_id = state.get("approval_request_id")
        if context is None or approval_request_id is None:
            return self._failure(
                code="AGENT_CONTEXT_MISSING",
                message="写工具执行缺少可信运行上下文",
            )
        try:
            result = await self._executor.execute(
                user_id=context.user_id,
                agent_run_id=context.agent_run_id,
                approval_request_id=approval_request_id,
                request_id=context.request_id,
            )
        except ApprovedActionExecutionError as exc:
            return self._failure(
                code=exc.code,
                message=exc.message,
                uncertain=exc.uncertain,
            )
        except AppException as exc:
            return self._failure(code=exc.code, message=exc.message)
        except Exception as exc:
            logger.exception(
                "审批后的写工具执行失败",
                extra={"node": self.name, "error_type": type(exc).__name__},
            )
            return self._failure(
                code="APPROVED_ACTION_EXECUTION_ERROR",
                message="审批后的写操作执行失败",
            )
        return {
            "execution_status": WriteExecutionStatus.SUCCEEDED,
            "execution": result.execution,
            "current_node": self.name,
        }

    def _failure(
        self,
        *,
        code: str,
        message: str,
        uncertain: bool = False,
    ) -> dict[str, object]:
        return {
            "execution_status": (
                WriteExecutionStatus.UNCERTAIN if uncertain else WriteExecutionStatus.FAILED
            ),
            "current_node": self.name,
            "error_code": code,
            "error_message": message,
        }
