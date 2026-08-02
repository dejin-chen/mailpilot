"""Agent Pydantic 结构化输出合同测试。"""

from datetime import UTC, datetime, timedelta

import pytest
from app.agent.schemas import (
    EmailAction,
    EmailCategory,
    EmailClassification,
    EmailPriority,
    ExecutionPlan,
    ExtractedIntent,
    MeetingIntent,
    PlanAction,
    PlanStep,
    ReadOnlyToolName,
)
from pydantic import ValidationError


def test_email_classification_accepts_only_bounded_structured_values() -> None:
    classification = EmailClassification(
        action=EmailAction.REPLY,
        priority=EmailPriority.HIGH,
        category=EmailCategory.MEETING,
        summary="客户要求确认会议时间",
        reason="正文包含明确的回复要求",
        confidence=0.93,
    )

    assert classification.action is EmailAction.REPLY
    assert classification.confidence == 0.93

    with pytest.raises(ValidationError):
        EmailClassification(
            action="reply",
            priority="非常紧急",
            category="meeting",
            summary="摘要",
            reason="原因",
            confidence=1.2,
        )


def test_complete_meeting_requires_aware_valid_time_range_and_timezone() -> None:
    start_at = datetime(2026, 7, 23, 7, 0, tzinfo=UTC)
    meeting = MeetingIntent(
        detected=True,
        title="项目确认会",
        start_at=start_at,
        end_at=start_at + timedelta(hours=1),
        timezone="Asia/Shanghai",
        attendees=["manager@example.com"],
        time_information_complete=True,
    )

    assert meeting.time_information_complete is True

    with pytest.raises(ValidationError, match="完整会议时间"):
        MeetingIntent(detected=True, time_information_complete=True)

    with pytest.raises(ValidationError, match="必须包含时区"):
        MeetingIntent(
            detected=True,
            start_at=datetime(2026, 7, 23, 15, 0),
            end_at=datetime(2026, 7, 23, 16, 0),
            timezone="Asia/Shanghai",
            time_information_complete=True,
        )


def test_clarification_intent_must_explain_missing_information() -> None:
    with pytest.raises(ValidationError, match="missing_information"):
        ExtractedIntent(
            needs_clarification=True,
            reason="邮件只说这周找个时间开会",
        )


def test_execution_plan_only_accepts_read_only_tools_and_contiguous_steps() -> None:
    plan = ExecutionPlan(
        goal="确认会议时间并准备回复草稿",
        steps=[
            PlanStep(
                sequence=1,
                action=PlanAction.READ_TOOL,
                description="检查用户日历是否冲突",
                tool_name=ReadOnlyToolName.CHECK_AVAILABILITY,
                tool_arguments={
                    "start_at": "2026-07-23T07:00:00Z",
                    "end_at": "2026-07-23T08:00:00Z",
                },
            ),
            PlanStep(
                sequence=2,
                action=PlanAction.GENERATE_DRAFT,
                description="根据日历结果生成回复草稿",
            ),
        ],
        should_generate_draft=True,
    )

    assert plan.expected_tool_calls == 1

    with pytest.raises(ValidationError):
        PlanStep(
            sequence=1,
            action="read_tool",
            description="尝试发送邮件",
            tool_name="send_email",
        )

    with pytest.raises(ValidationError):
        PlanStep(
            sequence=1,
            action=PlanAction.READ_TOOL,
            description="使用错误参数检查日历",
            tool_name=ReadOnlyToolName.CHECK_AVAILABILITY,
            tool_arguments={
                "time": "2026-07-23T07:00:00Z",
                "end_time": "2026-07-23T08:00:00Z",
            },
        )

    with pytest.raises(ValidationError, match="sequence"):
        ExecutionPlan(
            goal="错误顺序",
            steps=[
                PlanStep(
                    sequence=2,
                    action=PlanAction.FINALIZE,
                    description="结束",
                )
            ],
            should_generate_draft=False,
        )

    with pytest.raises(ValidationError, match="should_generate_draft"):
        ExecutionPlan(
            goal="追问缺少信息",
            steps=[
                PlanStep(
                    sequence=1,
                    action=PlanAction.REQUEST_CLARIFICATION,
                    description="询问会议时间",
                )
            ],
            should_generate_draft=False,
            needs_clarification=True,
        )
