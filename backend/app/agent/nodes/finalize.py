"""把工作流当前 State 整理成稳定最终结果。"""

from typing import Literal

from app.agent.nodes.common import NodeUpdate
from app.agent.schemas import AgentRunStatus, EmailClassification, FinalResult
from app.agent.state import MailAgentState

FinalizeOutcome = Literal["success", "ignored", "failed"]


class FinalizeNode:
    """按确定结果生成摘要；它不调用模型、数据库或外部工具。"""

    def __init__(self, outcome: FinalizeOutcome) -> None:
        self.outcome = outcome
        self.name = f"finalize_{outcome}"

    def __call__(self, state: MailAgentState) -> NodeUpdate:
        if self.outcome == "failed":
            return self._failed_update(state)
        if self.outcome == "ignored":
            return self._ignored_update(state)
        return self._success_update(state)

    def _success_update(self, state: MailAgentState) -> NodeUpdate:
        tool_results = state.get("tool_results", [])
        successful_calls = sum(result.success for result in tool_results)
        if tool_results:
            summary = f"邮件分析完成，成功执行 {successful_calls} 个只读工具"
        else:
            summary = "邮件分析完成，无需执行只读工具"
        final_result = FinalResult(
            status=AgentRunStatus.COMPLETED,
            summary=summary,
            draft_ready="draft" in state,
            requires_future_approval=False,
        )
        return {
            "current_node": self.name,
            "run_status": AgentRunStatus.COMPLETED,
            "final_result": final_result,
        }

    def _ignored_update(self, state: MailAgentState) -> NodeUpdate:
        classification = state.get("classification")
        if isinstance(classification, EmailClassification):
            summary = f"邮件无需处理：{classification.summary}"
        else:
            summary = "邮件无需处理"
        final_result = FinalResult(
            status=AgentRunStatus.IGNORED,
            summary=summary,
        )
        return {
            "current_node": self.name,
            "run_status": AgentRunStatus.IGNORED,
            "final_result": final_result,
        }

    def _failed_update(self, state: MailAgentState) -> NodeUpdate:
        errors = state.get("errors", [])
        if errors:
            summary = f"Agent 执行失败：{errors[-1].message}"
        else:
            summary = "Agent 执行失败：工作流缺少必要结果"
        final_result = FinalResult(
            status=AgentRunStatus.FAILED,
            summary=summary,
        )
        return {
            "current_node": self.name,
            "run_status": AgentRunStatus.FAILED,
            "final_result": final_result,
        }
