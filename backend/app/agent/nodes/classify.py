"""邮件分类和优先级结构化输出节点。"""

import logging

from app.agent.nodes.common import NodeUpdate, llm_failure_update
from app.agent.prompts import build_classification_messages
from app.agent.schemas import AgentRunStatus, EmailClassification
from app.agent.state import MailAgentState
from app.integrations.llm.client import StructuredLlmClient

logger = logging.getLogger(__name__)


class ClassifyEmailNode:
    """调用可替换模型接口，并把分类结果与用量写入 State。"""

    name = "classify_email"

    def __init__(self, llm_client: StructuredLlmClient) -> None:
        self._llm_client = llm_client

    async def __call__(self, state: MailAgentState) -> NodeUpdate:
        try:
            result = await self._llm_client.ainvoke_structured(
                operation=self.name,
                messages=build_classification_messages(state),
                schema=EmailClassification,
            )
        except Exception as exc:
            return llm_failure_update(node=self.name, exc=exc, logger=logger)
        return {
            "classification": result.parsed,
            "model_usages": [result.usage],
            "current_node": self.name,
            "run_status": AgentRunStatus.RUNNING,
        }
