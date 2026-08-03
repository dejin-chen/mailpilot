"""用一次结构化模型调用完成意图提取与受控计划决策。"""

import logging

from langgraph.runtime import Runtime

from app.agent.context import AgentRuntimeContext
from app.agent.nodes.common import NodeUpdate, failure_update, llm_failure_update
from app.agent.planning import build_execution_plan
from app.agent.prompts import build_intent_plan_messages
from app.agent.schemas import (
    AgentRunStatus,
    EmailClassification,
    IntentPlanDecision,
)
from app.agent.security import validate_plan_policy
from app.agent.state import MailAgentState
from app.integrations.llm.client import StructuredLlmClient

logger = logging.getLogger(__name__)


class AnalyzeIntentPlanNode:
    """合并两个串行模型步骤，并由 Python 构造和校验最终计划。"""

    name = "analyze_intent_plan"

    def __init__(self, llm_client: StructuredLlmClient) -> None:
        self._llm_client = llm_client

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
        classification = state.get("classification")
        if not isinstance(classification, EmailClassification):
            return failure_update(
                node=self.name,
                code="AGENT_STATE_DATA_MISSING",
                message="合并分析前缺少结构化分类结果",
            )
        try:
            result = await self._llm_client.ainvoke_structured(
                operation=self.name,
                messages=build_intent_plan_messages(
                    state,
                    user_timezone=context.user_timezone,
                ),
                schema=IntentPlanDecision,
            )
        except Exception as exc:
            return llm_failure_update(node=self.name, exc=exc, logger=logger)

        intent = result.parsed.intent
        plan = build_execution_plan(
            decision=result.parsed,
            classification=classification,
        )
        violation = validate_plan_policy(
            plan=plan,
            classification=classification,
            intent=intent,
            current_thread_id=state["email_thread_id"],
            remaining_tool_calls=state.get("max_tool_calls", 4)
            - state.get("tool_call_count", 0),
        )
        if violation is not None:
            return failure_update(
                node=self.name,
                code=violation.code,
                message=violation.message,
            )
        return {
            "intent": intent,
            "plan": plan,
            "model_usages": [result.usage],
            "current_node": self.name,
            "run_status": AgentRunStatus.RUNNING,
        }
