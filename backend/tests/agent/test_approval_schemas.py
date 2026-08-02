"""审批写操作方案的结构化参数测试。"""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from app.agent.approval_schemas import (
    ApprovalInterruptPayload,
    CancelEventArguments,
    CreateEventArguments,
    SendEmailArguments,
    WriteActionProposal,
)
from app.models.approval import ApprovalAction
from pydantic import ValidationError


def test_write_action_requires_matching_argument_model() -> None:
    """操作名不能和另一种工具的参数混用。"""

    with pytest.raises(ValidationError, match="参数结构不正确"):
        WriteActionProposal(
            action=ApprovalAction.SEND_EMAIL,
            arguments=CancelEventArguments(event_id=uuid4()),
            summary="发送回复",
        )


def test_create_event_requires_aware_valid_time_range() -> None:
    """会议写操作在进入审批前就拒绝无时区时间。"""

    start_at = datetime(2026, 7, 25, 9, 0)
    with pytest.raises(ValidationError, match="必须包含时区"):
        CreateEventArguments(
            title="项目同步会",
            start_at=start_at,
            end_at=start_at + timedelta(hours=1),
            timezone="Asia/Shanghai",
        )


def test_interrupt_payload_is_json_serializable() -> None:
    """传给 interrupt 的 UUID 和枚举最终都转成普通 JSON 值。"""

    approval_id = uuid4()
    run_id = uuid4()
    payload = ApprovalInterruptPayload(
        approval_request_id=approval_id,
        agent_run_id=run_id,
        action=ApprovalAction.SEND_EMAIL,
        proposed_arguments=SendEmailArguments(draft_message_id=uuid4()).model_dump(mode="json"),
        summary="发送回复邮件",
        version=1,
    ).model_dump(mode="json")

    assert payload["approval_request_id"] == str(approval_id)
    assert payload["agent_run_id"] == str(run_id)
    assert payload["action"] == "send_email"


def test_create_event_accepts_valid_aware_time_range() -> None:
    start_at = datetime(2026, 7, 25, 1, 0, tzinfo=UTC)
    arguments = CreateEventArguments(
        title="项目同步会",
        start_at=start_at,
        end_at=start_at + timedelta(hours=1),
        attendees=["manager@example.com"],
        timezone="Asia/Shanghai",
    )

    assert arguments.end_at > arguments.start_at
