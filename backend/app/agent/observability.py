"""为异步或同步 LangGraph 节点增加统一 Span。"""

import inspect
from collections.abc import Awaitable, Callable
from typing import Any

from langgraph.runtime import Runtime

from app.agent.context import AgentRuntimeContext
from app.agent.state import MailAgentState
from app.observability.base import Observability

ObservedNode = Callable[
    [MailAgentState, Runtime[AgentRuntimeContext]],
    Awaitable[dict[str, Any]],
]


def observe_agent_node(
    *,
    name: str,
    node: Callable[..., Any],
    observability: Observability,
) -> ObservedNode:
    """保留节点原有职责，只在外层记录输入结构、结果结构和异常。"""

    accepts_runtime = "runtime" in inspect.signature(node).parameters

    async def observed(
        state: MailAgentState,
        runtime: Runtime[AgentRuntimeContext],
    ) -> dict[str, Any]:
        with observability.span(
            name=f"node.{name}",
            input={"state_keys": sorted(state.keys())},
            metadata={"node_name": name},
        ) as span:
            try:
                raw_result = node(state, runtime) if accepts_runtime else node(state)
                result = await raw_result if inspect.isawaitable(raw_result) else raw_result
            except Exception as exc:
                span.update(
                    level="ERROR",
                    status_message="LangGraph 节点执行失败",
                    metadata={"error_type": type(exc).__name__},
                )
                raise
            result_mapping = dict(result)
            span.update(
                output=result_mapping,
                metadata={
                    "result_keys": sorted(result_mapping),
                    "run_status": str(result_mapping.get("run_status", "")),
                },
            )
            return result_mapping

    observed.__name__ = f"observed_{name}"
    return observed
