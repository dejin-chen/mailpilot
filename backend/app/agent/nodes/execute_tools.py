"""按结构化计划执行有上限的只读 MCP 工具。"""

import logging
from collections.abc import Callable
from time import perf_counter
from typing import Any, Protocol

from langgraph.runtime import Runtime

from app.agent.context import AgentRuntimeContext
from app.agent.nodes.common import NodeUpdate, failure_update
from app.agent.schemas import (
    AgentRunStatus,
    ExecutionPlan,
    PlanAction,
    ToolExecutionResult,
)
from app.agent.state import MailAgentState
from app.core.exceptions import AppException
from app.integrations.mcp.results import (
    extract_mcp_business_error,
    normalize_mcp_output,
)

logger = logging.getLogger(__name__)


class ReadOnlyToolClient(Protocol):
    """工具执行节点真正依赖的最小 MCP Client 接口。"""

    async def invoke_tool(self, tool_name: str, arguments: dict[str, Any]) -> Any: ...


ToolClientFactory = Callable[[AgentRuntimeContext], ReadOnlyToolClient]


class ExecuteReadToolsNode:
    """只执行计划中的只读步骤，并在首次失败后确定性停止。"""

    name = "execute_read_tools"

    def __init__(self, client_factory: ToolClientFactory) -> None:
        self._client_factory = client_factory

    async def __call__(
        self,
        state: MailAgentState,
        runtime: Runtime[AgentRuntimeContext],
    ) -> NodeUpdate:
        context = runtime.context
        if context is None:
            return failure_update(
                node=self.name,
                code="AGENT_CONTEXT_MISSING",
                message="Agent 运行时身份缺失",
            )
        plan = state.get("plan")
        if not isinstance(plan, ExecutionPlan):
            return failure_update(
                node=self.name,
                code="AGENT_STATE_DATA_MISSING",
                message="工作流缺少必要数据：plan",
            )

        try:
            client = self._client_factory(context)
        except AppException as exc:
            return failure_update(node=self.name, code=exc.code, message=exc.message)
        except Exception as exc:
            logger.exception(
                "创建只读 MCP Client 失败",
                extra={"node": self.name, "error_type": type(exc).__name__},
            )
            return failure_update(
                node=self.name,
                code="MCP_CLIENT_CREATION_ERROR",
                message="创建 MCP Client 失败",
            )

        results: list[ToolExecutionResult] = []
        call_count = state.get("tool_call_count", 0)
        max_tool_calls = state.get("max_tool_calls", 4)
        for step in plan.steps:
            if step.action is not PlanAction.READ_TOOL:
                continue
            if call_count >= max_tool_calls:
                return self._failed_update(
                    results=results,
                    call_count=call_count,
                    code="TOOL_CALL_LIMIT_EXCEEDED",
                    message="工具调用次数已达到本次工作流上限",
                )

            tool_name = step.tool_name
            tool_arguments = step.tool_arguments
            if tool_name is None or tool_arguments is None:
                return self._failed_update(
                    results=results,
                    call_count=call_count,
                    code="INVALID_TOOL_PLAN",
                    message="只读工具步骤缺少工具名称或参数",
                )

            arguments = tool_arguments.model_dump(mode="json", exclude_none=True)
            started_at = perf_counter()
            call_count += 1
            try:
                raw_output = await client.invoke_tool(tool_name.value, arguments)
                output = normalize_mcp_output(raw_output)
            except AppException as exc:
                duration_ms = (perf_counter() - started_at) * 1000
                results.append(
                    ToolExecutionResult(
                        tool_name=tool_name,
                        success=False,
                        error_code=exc.code,
                        error_message=exc.message,
                        duration_ms=duration_ms,
                    )
                )
                return self._failed_update(
                    results=results,
                    call_count=call_count,
                    code=exc.code,
                    message=exc.message,
                    retryable=exc.status_code >= 500,
                )
            except Exception as exc:
                duration_ms = (perf_counter() - started_at) * 1000
                logger.warning(
                    "Agent 只读工具调用失败",
                    extra={
                        "node": self.name,
                        "tool_name": tool_name.value,
                        "error_type": type(exc).__name__,
                    },
                )
                results.append(
                    ToolExecutionResult(
                        tool_name=tool_name,
                        success=False,
                        error_code="MCP_TOOL_EXECUTION_ERROR",
                        error_message="MCP 工具执行失败",
                        duration_ms=duration_ms,
                    )
                )
                return self._failed_update(
                    results=results,
                    call_count=call_count,
                    code="MCP_TOOL_EXECUTION_ERROR",
                    message="MCP 工具执行失败",
                    retryable=True,
                )

            duration_ms = (perf_counter() - started_at) * 1000
            business_error = extract_mcp_business_error(output)
            if business_error is not None:
                code, message = business_error
                results.append(
                    ToolExecutionResult(
                        tool_name=tool_name,
                        success=False,
                        error_code=code,
                        error_message=message,
                        duration_ms=duration_ms,
                    )
                )
                return self._failed_update(
                    results=results,
                    call_count=call_count,
                    code=code,
                    message=message,
                )

            results.append(
                ToolExecutionResult(
                    tool_name=tool_name,
                    success=True,
                    output=output,
                    duration_ms=duration_ms,
                )
            )

        return {
            "tool_results": results,
            "tool_call_count": call_count,
            "current_node": self.name,
            "run_status": AgentRunStatus.RUNNING,
        }

    def _failed_update(
        self,
        *,
        results: list[ToolExecutionResult],
        call_count: int,
        code: str,
        message: str,
        retryable: bool = False,
    ) -> NodeUpdate:
        """失败时同时保留已经发生的工具调用记录和计数。"""

        update = failure_update(
            node=self.name,
            code=code,
            message=message,
            retryable=retryable,
        )
        update["tool_results"] = results
        update["tool_call_count"] = call_count
        return update
