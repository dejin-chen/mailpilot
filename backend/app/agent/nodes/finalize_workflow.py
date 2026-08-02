"""完整邮件工作流的确定性终态节点。"""

from typing import Literal

from app.agent.approval_schemas import WriteExecutionStatus
from app.agent.nodes.common import NodeUpdate
from app.agent.schemas import AgentRunStatus, EmailClassification, FinalResult
from app.agent.state import MailAgentState

WorkflowOutcome = Literal["success", "ignored", "failed", "cancelled"]


class FinalizeWorkflowNode:
    """把分析、审批和写执行事实整理成稳定的最终结果。"""

    def __init__(self, outcome: WorkflowOutcome) -> None:
        self.outcome = outcome
        self.name = f"finalize_{outcome}"

    def __call__(self, state: MailAgentState) -> NodeUpdate:
        if self.outcome == "ignored":
            classification = state.get("classification")
            summary = (
                f"邮件无需处理：{classification.summary}"
                if isinstance(classification, EmailClassification)
                else "邮件无需处理"
            )
            status = AgentRunStatus.IGNORED
        elif self.outcome == "cancelled":
            summary = "用户拒绝了写操作，本次邮件处理已取消"
            status = AgentRunStatus.CANCELLED
        elif self.outcome == "failed":
            errors = state.get("errors", [])
            message = (
                errors[-1].message if errors else state.get("error_message", "工作流缺少必要结果")
            )
            summary = f"Agent 执行失败：{message}"
            status = AgentRunStatus.FAILED
        else:
            execution_status = state.get("execution_status")
            if execution_status is WriteExecutionStatus.SUCCEEDED:
                summary = "邮件分析、人工审批和写操作执行完成"
            else:
                summary = "邮件分析完成，无需执行需要审批的写操作"
            status = AgentRunStatus.COMPLETED

        return {
            "current_node": self.name,
            "run_status": status,
            "final_result": FinalResult(
                status=status,
                summary=summary,
                draft_ready="draft" in state,
                requires_future_approval=False,
            ),
        }
