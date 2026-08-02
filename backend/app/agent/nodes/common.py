"""多个 Agent 节点共用的安全失败更新。"""

import logging

from app.agent.exceptions import AgentStateDataError
from app.agent.schemas import AgentError, AgentRunStatus
from app.integrations.llm.exceptions import (
    LlmConfigurationError,
    LlmInvocationError,
    LlmStructuredOutputError,
)

NodeUpdate = dict[str, object]


def failure_update(
    *,
    node: str,
    code: str,
    message: str,
    retryable: bool = False,
) -> NodeUpdate:
    """生成能够被后续失败路由识别的统一 State 更新。"""

    return {
        "current_node": node,
        "run_status": AgentRunStatus.FAILED,
        "errors": [
            AgentError(
                node=node,
                code=code,
                message=message,
                retryable=retryable,
            )
        ],
    }


def llm_failure_update(*, node: str, exc: Exception, logger: logging.Logger) -> NodeUpdate:
    """把模型和 State 异常映射为不含邮件正文的稳定错误。"""

    if isinstance(exc, LlmConfigurationError):
        return failure_update(
            node=node,
            code="LLM_CONFIGURATION_ERROR",
            message="模型配置不完整",
        )
    if isinstance(exc, LlmInvocationError):
        return failure_update(
            node=node,
            code="LLM_INVOCATION_ERROR",
            message="模型服务调用失败",
            retryable=True,
        )
    if isinstance(exc, LlmStructuredOutputError):
        return failure_update(
            node=node,
            code="LLM_STRUCTURED_OUTPUT_ERROR",
            message="模型返回的结构化结果无效",
        )
    if isinstance(exc, AgentStateDataError):
        return failure_update(
            node=node,
            code="AGENT_STATE_DATA_MISSING",
            message=f"工作流缺少必要数据：{exc.field_name}",
        )

    logger.exception(
        "Agent 节点发生未预期异常",
        extra={"node": node, "error_type": type(exc).__name__},
    )
    return failure_update(
        node=node,
        code="AGENT_NODE_ERROR",
        message="Agent 节点执行失败",
    )
