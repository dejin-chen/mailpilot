"""任务、会议、时间与待追问信息提取节点。"""

import logging

from langgraph.runtime import Runtime

from app.agent.context import AgentRuntimeContext
from app.agent.nodes.common import NodeUpdate, failure_update, llm_failure_update
from app.agent.prompts import build_intent_messages
from app.agent.schemas import AgentRunStatus, ExtractedIntent
from app.agent.state import MailAgentState
from app.integrations.llm.client import StructuredLlmClient

logger = logging.getLogger(__name__)


class ExtractIntentNode:
    """使用邮件发送时间和可信用户时区解释相对时间。"""

    name = "extract_intent"

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
        try:
            result = await self._llm_client.ainvoke_structured(
                operation=self.name,
                messages=build_intent_messages(
                    state,
                    user_timezone=context.user_timezone,
                ),
                schema=ExtractedIntent,
            )
        except Exception as exc:
            return llm_failure_update(node=self.name, exc=exc, logger=logger)
        return {
            "intent": result.parsed,
            "model_usages": [result.usage],
            "current_node": self.name,
            "run_status": AgentRunStatus.RUNNING,
        }
