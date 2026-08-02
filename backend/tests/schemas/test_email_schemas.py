"""邮件 Schema 测试。"""

from datetime import datetime

import pytest
from app.schemas.email import EmailMessageImport
from pydantic import ValidationError


def _valid_payload() -> dict[str, object]:
    return {
        "provider": " LOCAL ",
        "thread_external_id": " thread-001 ",
        "message_external_id": " message-001 ",
        "subject": " 项目周会安排 ",
        "sender": " BOSS@Example.com ",
        "recipients": [" USER@Example.com "],
        "cc": [" TEAM@Example.com "],
        "body_text": "请确认明天下午是否可以开会。",
        "headers": {"Message-ID": "message-001"},
        "sent_at": "2026-07-19T10:00:00+08:00",
    }


def test_email_import_normalizes_provider_ids_and_addresses() -> None:
    data = EmailMessageImport.model_validate(_valid_payload())

    assert data.provider == "local"
    assert data.thread_external_id == "thread-001"
    assert data.message_external_id == "message-001"
    assert data.subject == "项目周会安排"
    assert str(data.sender) == "boss@example.com"
    assert [str(item) for item in data.recipients] == ["user@example.com"]
    assert [str(item) for item in data.cc] == ["team@example.com"]
    assert data.sent_at.utcoffset() is not None


def test_email_import_rejects_time_without_timezone() -> None:
    payload = _valid_payload()
    payload["sent_at"] = datetime(2026, 7, 19, 10, 0)

    with pytest.raises(ValidationError, match="邮件时间必须包含时区"):
        EmailMessageImport.model_validate(payload)


@pytest.mark.parametrize("provider", ["Gmail.com", "1local", "local provider"])
def test_email_import_rejects_unsafe_provider_name(provider: str) -> None:
    payload = _valid_payload()
    payload["provider"] = provider

    with pytest.raises(ValidationError):
        EmailMessageImport.model_validate(payload)


def test_email_import_requires_at_least_one_recipient() -> None:
    payload = _valid_payload()
    payload["recipients"] = []

    with pytest.raises(ValidationError):
        EmailMessageImport.model_validate(payload)
