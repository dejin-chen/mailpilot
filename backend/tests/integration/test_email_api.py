"""邮件 API、JWT 与用户隔离集成测试。"""

from datetime import UTC, datetime
from uuid import UUID

import pytest
from app.schemas.email import EmailMessageImport
from app.schemas.user import UserCreate
from app.security.jwt import create_access_token
from app.services.email import EmailService
from app.services.user import UserService
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = pytest.mark.integration


def _user_create(email: str) -> UserCreate:
    return UserCreate(
        email=email,
        password="integration-password",
        full_name="邮件 API 用户",
        timezone="Asia/Shanghai",
    )


def _email_payload() -> dict[str, object]:
    return {
        "provider": "local",
        "thread_external_id": "api-thread-001",
        "message_external_id": "api-message-001",
        "subject": "API 项目周会",
        "sender": "boss@example.com",
        "recipients": ["email-api@example.com"],
        "cc": [],
        "body_text": "请确认会议时间。",
        "headers": {"Message-ID": "api-message-001"},
        "sent_at": "2026-07-20T10:00:00+08:00",
    }


def _auth_headers(user_id: UUID) -> dict[str, str]:
    return {"Authorization": f"Bearer {create_access_token(user_id)}"}


async def test_import_list_and_get_email_thread(
    integration_session: AsyncSession,
    integration_client: AsyncClient,
) -> None:
    user = await UserService(integration_session).create_user(_user_create("email-api@example.com"))
    headers = _auth_headers(user.id)

    imported = await integration_client.post(
        "/api/v1/emails/import",
        json=_email_payload(),
        headers={**headers, "X-Request-ID": "email-import-001"},
    )

    assert imported.status_code == 201
    imported_data = imported.json()["data"]
    assert imported_data["created_thread"] is True
    assert imported_data["message"]["direction"] == "inbound"
    assert imported.json()["request_id"] == "email-import-001"

    listed = await integration_client.get("/api/v1/emails", headers=headers)
    assert listed.status_code == 200
    assert listed.json()["data"]["total"] == 1
    assert len(listed.json()["data"]["items"]) == 1

    detailed = await integration_client.get(
        f"/api/v1/emails/{imported_data['thread']['id']}",
        headers=headers,
    )
    assert detailed.status_code == 200
    assert len(detailed.json()["data"]["messages"]) == 1


async def test_email_import_rejects_duplicate(
    integration_session: AsyncSession,
    integration_client: AsyncClient,
) -> None:
    user = await UserService(integration_session).create_user(
        _user_create("email-duplicate-api@example.com")
    )
    headers = _auth_headers(user.id)
    await integration_client.post("/api/v1/emails/import", json=_email_payload(), headers=headers)

    duplicated = await integration_client.post(
        "/api/v1/emails/import",
        json=_email_payload(),
        headers=headers,
    )

    assert duplicated.status_code == 409
    assert duplicated.json()["error"]["code"] == "EMAIL_ALREADY_IMPORTED"


async def test_email_api_hides_another_users_thread(
    integration_session: AsyncSession,
    integration_client: AsyncClient,
) -> None:
    owner = await UserService(integration_session).create_user(
        _user_create("email-owner-api@example.com")
    )
    stranger = await UserService(integration_session).create_user(
        _user_create("email-stranger-api@example.com")
    )
    imported = await EmailService(integration_session).import_inbound_email(
        user_id=owner.id,
        data=EmailMessageImport.model_validate(
            {
                **_email_payload(),
                "sent_at": datetime(2026, 7, 20, 2, 0, tzinfo=UTC),
            }
        ),
    )
    stranger_headers = _auth_headers(stranger.id)

    detailed = await integration_client.get(
        f"/api/v1/emails/{imported.thread.id}",
        headers=stranger_headers,
    )
    listed = await integration_client.get("/api/v1/emails", headers=stranger_headers)

    assert detailed.status_code == 404
    assert detailed.json()["error"]["code"] == "EMAIL_THREAD_NOT_FOUND"
    assert listed.json()["data"]["total"] == 0


async def test_email_api_requires_login(integration_client: AsyncClient) -> None:
    response = await integration_client.get("/api/v1/emails")

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "AUTHENTICATION_REQUIRED"
