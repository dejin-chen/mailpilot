"""长期记忆 Patch 与模型判断 Schema 测试。"""

import pytest
from app.models.memory import MemoryType
from app.schemas.memory_feedback import (
    CalendarPreferencesMemoryPatch,
    EmailStyleMemoryPatch,
    EmailStyleMemoryUpdateProposal,
    FeedbackMemoryDecision,
)
from pydantic import ValidationError


def test_patch_rejects_empty_or_unknown_fields() -> None:
    with pytest.raises(ValidationError):
        EmailStyleMemoryPatch()
    with pytest.raises(ValidationError):
        EmailStyleMemoryPatch.model_validate({"tone": "专业", "unknown": "bad"})


def test_calendar_patch_validates_timezone_and_duration() -> None:
    with pytest.raises(ValidationError):
        CalendarPreferencesMemoryPatch(timezone="Mars/Office")
    with pytest.raises(ValidationError):
        CalendarPreferencesMemoryPatch(default_duration_minutes=3)


def test_decision_requires_proposal_only_when_updating() -> None:
    proposal = EmailStyleMemoryUpdateProposal(
        memory_type=MemoryType.EMAIL_STYLE,
        patch=EmailStyleMemoryPatch(tone="专业简洁"),
        evidence="以后都写得专业简洁",
        reason="明确长期风格",
    )
    with pytest.raises(ValidationError):
        FeedbackMemoryDecision(
            should_update=True,
            proposal=None,
            reason="错误示例",
            confidence=0.9,
        )
    with pytest.raises(ValidationError):
        FeedbackMemoryDecision(
            should_update=False,
            proposal=proposal,
            reason="错误示例",
            confidence=0.9,
        )
