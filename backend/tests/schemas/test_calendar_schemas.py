"""日历 Schema 测试。"""

from datetime import datetime

import pytest
from app.schemas.calendar import CalendarEventImport
from pydantic import ValidationError


def _valid_payload() -> dict[str, object]:
    return {
        "provider": " LOCAL ",
        "external_id": " event-001 ",
        "title": " 项目周会 ",
        "description": "讨论项目进度",
        "start_at": "2026-07-20T10:00:00+08:00",
        "end_at": "2026-07-20T11:00:00+08:00",
        "timezone": "Asia/Shanghai",
        "attendees": [" TEAM@Example.com "],
        "location": " 3A 会议室 ",
        "idempotency_key": " calendar-import-001 ",
    }


def test_calendar_import_normalizes_safe_fields() -> None:
    data = CalendarEventImport.model_validate(_valid_payload())

    assert data.provider == "local"
    assert data.external_id == "event-001"
    assert data.title == "项目周会"
    assert [str(item) for item in data.attendees] == ["team@example.com"]
    assert data.location == "3A 会议室"
    assert data.idempotency_key == "calendar-import-001"


@pytest.mark.parametrize(
    ("start_at", "end_at"),
    [
        ("2026-07-20T11:00:00+08:00", "2026-07-20T10:00:00+08:00"),
        ("2026-07-20T10:00:00+08:00", "2026-07-20T10:00:00+08:00"),
        (datetime(2026, 7, 20, 10), datetime(2026, 7, 20, 11)),
    ],
)
def test_calendar_import_rejects_invalid_time_range(
    start_at: object,
    end_at: object,
) -> None:
    payload = _valid_payload()
    payload.update(start_at=start_at, end_at=end_at)

    with pytest.raises(ValidationError):
        CalendarEventImport.model_validate(payload)


def test_calendar_import_rejects_invalid_timezone() -> None:
    payload = _valid_payload()
    payload["timezone"] = "Mars/Office"

    with pytest.raises(ValidationError, match="无效时区"):
        CalendarEventImport.model_validate(payload)


def test_calendar_import_rejects_blank_title() -> None:
    payload = _valid_payload()
    payload["title"] = "   "

    with pytest.raises(ValidationError, match="日程标题不能为空"):
        CalendarEventImport.model_validate(payload)
