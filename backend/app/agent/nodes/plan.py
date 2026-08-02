"""把分类和意图转换为只读、可计数的结构化计划。"""

import logging

from app.agent.nodes.common import NodeUpdate, failure_update, llm_failure_update
from app.agent.prompts import build_plan_messages
from app.agent.schemas import (
    AgentRunStatus,
    EmailClassification,
    ExecutionPlan,
    ExtractedIntent,
)
from app.agent.security import validate_plan_policy
from app.agent.state import MailAgentState
from app.integrations.llm.client import StructuredLlmClient

logger = logging.getLogger(__name__)


class BuildPlanNode:
    """生成计划后再次检查它没有超过本次工作流工具上限。"""

    name = "build_plan"

    def __init__(self, llm_client: StructuredLlmClient) -> None:
        self._llm_client = llm_client

    async def __call__(self, state: MailAgentState) -> NodeUpdate:
        try:
            result = await self._llm_client.ainvoke_structured(
                operation=self.name,
                messages=build_plan_messages(state),
                schema=ExecutionPlan,
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
                message="计划安全校验前缺少结构化分类或意图",
            )

        violation = validate_plan_policy(
            plan=result.parsed,
            classification=classification,
            intent=intent,
            current_thread_id=state["email_thread_id"],
            remaining_tool_calls=state.get("max_tool_calls", 4) - state.get("tool_call_count", 0),
        )
        if violation is not None:
            return failure_update(
                node=self.name,
                code=violation.code,
                message=violation.message,
            )
        return {
            "plan": result.parsed,
            "model_usages": [result.usage],
            "current_node": self.name,
            "run_status": AgentRunStatus.RUNNING,
        }
