"""AgentRun 与审批输入 Schema 测试。"""

from uuid import uuid4

import pytest
from app.models.approval import ApprovalStatus
from app.schemas.agent_run import AgentRunCreate
from app.schemas.approval import ApprovalDecision
from pydantic import ValidationError


def test_agent_run_create_trims_graph_thread_id() -> None:
    data = AgentRunCreate(
        email_thread_id=uuid4(),
        graph_thread_id="  graph-thread-001  ",
    )

    assert data.graph_thread_id == "graph-thread-001"


def test_feedback_decision_requires_text() -> None:
    with pytest.raises(ValidationError, match="必须提供反馈"):
        ApprovalDecision(status=ApprovalStatus.FEEDBACK_REQUESTED, feedback="   ")


def test_only_approved_decision_accepts_modified_arguments() -> None:
    approved = ApprovalDecision(
        status=ApprovalStatus.APPROVED,
        modified_arguments={"subject": "修改后的主题"},
    )

    assert approved.modified_arguments == {"subject": "修改后的主题"}
    with pytest.raises(ValidationError, match="修改后接受"):
        ApprovalDecision(
            status=ApprovalStatus.REJECTED,
            modified_arguments={"subject": "不应接受"},
        )


def test_user_cannot_submit_internal_execution_status() -> None:
    with pytest.raises(ValidationError, match="只能接受、拒绝"):
        ApprovalDecision(status=ApprovalStatus.EXECUTED)
