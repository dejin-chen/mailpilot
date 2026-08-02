"""长期记忆结构化 Schema 测试。"""

import pytest
from app.models.memory import MemoryType
from app.schemas.memory import (
    CalendarPreferencesMemory,
    ContactMemory,
    EmailStyleMemory,
    MemoryProfileCreate,
)
from pydantic import ValidationError


def test_email_style_requires_at_least_one_preference() -> None:
    with pytest.raises(ValidationError):
        EmailStyleMemory()


def test_calendar_preferences_normalize_weekdays_and_validate_timezone() -> None:
    memory = CalendarPreferencesMemory(
        timezone="Asia/Shanghai",
        preferred_windows=[
            {
                "weekdays": [5, 1, 1],
                "start_time": "09:00:00",
                "end_time": "12:00:00",
            }
        ],
        default_duration_minutes=30,
    )

    assert memory.preferred_windows[0].weekdays == [1, 5]
    assert memory.model_dump(mode="json")["preferred_windows"][0]["start_time"] == "09:00:00"


@pytest.mark.parametrize(
    ("payload", "expected_message"),
    [
        (
            {
                "timezone": "Not/A-Timezone",
            },
            "无效时区",
        ),
        (
            {
                "preferred_windows": [
                    {
                        "weekdays": [1],
                        "start_time": "18:00:00",
                        "end_time": "09:00:00",
                    }
                ],
            },
            "结束时间必须晚于开始时间",
        ),
    ],
)
def test_calendar_preferences_reject_invalid_values(
    payload: dict[str, object],
    expected_message: str,
) -> None:
    with pytest.raises(ValidationError, match=expected_message):
        CalendarPreferencesMemory.model_validate(payload)


def test_contact_memory_rejects_unknown_field() -> None:
    with pytest.raises(ValidationError):
        ContactMemory.model_validate(
            {
                "email": "manager@example.com",
                "private_note": "不允许的自由字段",
            }
        )


def test_profile_create_validates_value_against_selected_type() -> None:
    with pytest.raises(ValidationError):
        MemoryProfileCreate(
            memory_type=MemoryType.EMAIL_STYLE,
            value={"timezone": "Asia/Shanghai"},
        )
