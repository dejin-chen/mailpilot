"""合并分析决策转换为确定性执行计划的测试。"""

from datetime import UTC, datetime, timedelta

from app.agent.planning import build_execution_plan
from app.agent.schemas import (
    EmailAction,
    EmailCategory,
    EmailClassification,
    EmailPriority,
    ExtractedIntent,
    IntentPlanDecision,
    MeetingIntent,
    PlanAction,
    ReadOnlyToolName,
    ReadToolDecision,
)


def _classification(action: EmailAction = EmailAction.REPLY) -> EmailClassification:
    return EmailClassification(
        action=action,
        priority=EmailPriority.NORMAL,
        category=EmailCategory.MEETING,
        summary="会议请求",
        reason="邮件要求处理",
        confidence=0.9,
    )


def test_complete_meeting_uses_validated_intent_time_for_availability_check() -> None:
    start_at = datetime(2026, 8, 4, 6, 0, tzinfo=UTC)
    intent = ExtractedIntent(
        meeting=MeetingIntent(
            detected=True,
            start_at=start_at,
            end_at=start_at + timedelta(hours=1),
            timezone="Asia/Shanghai",
            time_information_complete=True,
        ),
        reason="会议时间完整",
    )
    decision = IntentPlanDecision(
        intent=intent,
        should_generate_draft=False,
        reason="查询日历",
    )

    plan = build_execution_plan(
        decision=decision,
        classification=_classification(),
    )

    availability_step = plan.steps[0]
    assert availability_step.tool_name is ReadOnlyToolName.CHECK_AVAILABILITY
    assert availability_step.tool_arguments.start_at == start_at  # type: ignore[union-attr]
    assert plan.should_generate_draft is True
    assert plan.steps[-1].action is PlanAction.GENERATE_DRAFT


def test_incomplete_meeting_removes_model_generated_calendar_guess() -> None:
    intent = ExtractedIntent(
        meeting=MeetingIntent(detected=True, time_information_complete=False),
        needs_clarification=True,
        missing_information=["会议开始时间", "会议结束时间"],
        reason="会议时间不完整",
    )
    decision = IntentPlanDecision(
        intent=intent,
        read_tools=[
            ReadToolDecision(
                tool_name=ReadOnlyToolName.CHECK_AVAILABILITY,
                tool_arguments={
                    "start_at": "2026-08-04T06:00:00+00:00",
                    "end_at": "2026-08-04T07:00:00+00:00",
                },
            )
        ],
        should_generate_draft=False,
        reason="需要追问",
    )

    plan = build_execution_plan(
        decision=decision,
        classification=_classification(EmailAction.REMIND),
    )

    assert plan.expected_tool_calls == 0
    assert plan.needs_clarification is True
    assert plan.steps[-1].action is PlanAction.REQUEST_CLARIFICATION
