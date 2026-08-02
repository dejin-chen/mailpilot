"""把 Graph State 转换为适合 AgentRun 持久化的安全结果。"""

from typing import Any

from pydantic_core import to_jsonable_python

from app.agent.schemas import ModelUsage
from app.models.agent_run import AgentWorkflowName

_PERSISTED_KEYS = (
    "classification",
    "intent",
    "plan",
    "memory_context",
    "tool_results",
    "draft",
    "draft_message_id",
    "proposal",
    "approval_request_id",
    "approval_status",
    "gate_status",
    "memory_feedback_decision",
    "memory_update",
    "execution_status",
    "execution",
    "current_node",
    "run_status",
    "final_result",
    "errors",
    "error_code",
    "error_message",
    "regeneration_count",
)


def build_persisted_agent_result(values: dict[str, Any]) -> dict[str, Any]:
    """只保存产品页面需要的结构化字段，不复制完整邮件正文和 Prompt。"""

    result: dict[str, Any] = {
        "workflow_name": AgentWorkflowName.MAIL_PROCESSING_V1.value,
    }
    for key in _PERSISTED_KEYS:
        if key in values:
            result[key] = to_jsonable_python(values[key])
    return result


def sum_model_usage(values: dict[str, Any]) -> tuple[int, int, int]:
    """汇总所有模型节点的 Token；缺失统计按零处理。"""

    usages = values.get("model_usages", [])
    validated: list[ModelUsage] = []
    for usage in usages if isinstance(usages, list) else []:
        try:
            validated.append(ModelUsage.model_validate(usage))
        except Exception:
            continue
    return (
        sum(item.input_tokens for item in validated),
        sum(item.output_tokens for item in validated),
        sum(item.total_tokens for item in validated),
    )
