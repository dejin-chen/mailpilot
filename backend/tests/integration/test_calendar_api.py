"""日历 API、冲突查询与用户隔离集成测试。"""

from datetime import UTC, datetime
from uuid import UUID

import pytest
from app.schemas.calendar import CalendarEventImport
from app.schemas.user import UserCreate
from app.security.jwt import create_access_token
from app.services.calendar import CalendarService
from app.services.user import UserService
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = pytest.mark.integration


def _user_create(email: str) -> UserCreate:
    return UserCreate(
        email=email,
        password="integration-password",
        full_name="日历 API 用户",
        timezone="Asia/Shanghai",
    )


def _calendar_payload(**changes: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "provider": "local",
        "external_id": "api-event-001",
        "title": "API 项目周会",
        "description": "讨论项目进度",
        "start_at": "2026-07-20T10:00:00+08:00",
        "end_at": "2026-07-20T11:00:00+08:00",
        "timezone": "Asia/Shanghai",
        "attendees": ["team@example.com"],
        "location": "3A 会议室",
        "idempotency_key": "api-event-import-001",
    }
    payload.update(changes)
    return payload


def _auth_headers(user_id: UUID) -> dict[str, str]:
    return {"Authorization": f"Bearer {create_access_token(user_id)}"}


async def test_import_list_get_and_check_calendar_conflict(
    integration_session: AsyncSession,
    integration_client: AsyncClient,
) -> None:
    user = await UserService(integration_session).create_user(
        _user_create("calendar-api@example.com")
    )
    headers = _auth_headers(user.id)

    imported = await integration_client.post(
        "/api/v1/calendar/events/import",
        json=_calendar_payload(),
        headers=headers,
    )
    assert imported.status_code == 201
    event_id = imported.json()["data"]["id"]

    listed = await integration_client.get("/api/v1/calendar/events", headers=headers)
    detailed = await integration_client.get(
        f"/api/v1/calendar/events/{event_id}",
        headers=headers,
    )
    conflict = await integration_client.get(
        "/api/v1/calendar/availability",
        params={
            "start_at": "2026-07-20T10:30:00+08:00",
            "end_at": "2026-07-20T10:45:00+08:00",
        },
        headers=headers,
    )
    adjacent = await integration_client.get(
        "/api/v1/calendar/availability",
        params={
            "start_at": "2026-07-20T11:00:00+08:00",
            "end_at": "2026-07-20T12:00:00+08:00",
        },
        headers=headers,
    )

    assert listed.status_code == 200
    assert listed.json()["data"]["total"] == 1
    assert detailed.status_code == 200
    assert conflict.json()["data"]["available"] is False
    assert len(conflict.json()["data"]["conflicts"]) == 1
    assert adjacent.json()["data"]["available"] is True


async def test_calendar_import_rejects_duplicate_idempotency_key(
    integration_session: AsyncSession,
    integration_client: AsyncClient,
) -> None:
    user = await UserService(integration_session).create_user(
        _user_create("calendar-duplicate-api@example.com")
    )
    headers = _auth_headers(user.id)
    await integration_client.post(
        "/api/v1/calendar/events/import",
        json=_calendar_payload(),
        headers=headers,
    )

    duplicated = await integration_client.post(
        "/api/v1/calendar/events/import",
        json=_calendar_payload(external_id="another-external-id"),
        headers=headers,
    )

    assert duplicated.status_code == 409
    assert duplicated.json()["error"]["code"] == "CALENDAR_EVENT_ALREADY_IMPORTED"


async def test_cancelled_event_does_not_block_availability(
    integration_session: AsyncSession,
    integration_client: AsyncClient,
) -> None:
    user = await UserService(integration_session).create_user(
        _user_create("calendar-cancelled-api@example.com")
    )
    headers = _auth_headers(user.id)
    await integration_client.post(
        "/api/v1/calendar/events/import",
        json=_calendar_payload(status="cancelled"),
        headers=headers,
    )

    response = await integration_client.get(
        "/api/v1/calendar/availability",
        params={
            "start_at": "2026-07-20T10:30:00+08:00",
            "end_at": "2026-07-20T10:45:00+08:00",
        },
        headers=headers,
    )

    assert response.status_code == 200
    assert response.json()["data"]["available"] is True


async def test_calendar_api_hides_another_users_event(
    integration_session: AsyncSession,
    integration_client: AsyncClient,
) -> None:
    owner = await UserService(integration_session).create_user(
        _user_create("calendar-owner-api@example.com")
    )
    stranger = await UserService(integration_session).create_user(
        _user_create("calendar-stranger-api@example.com")
    )
    event = await CalendarService(integration_session).import_event(
        user_id=owner.id,
        data=CalendarEventImport.model_validate(
            _calendar_payload(
                start_at=datetime(2026, 7, 20, 2, 0, tzinfo=UTC),
                end_at=datetime(2026, 7, 20, 3, 0, tzinfo=UTC),
            )
        ),
    )
    stranger_headers = _auth_headers(stranger.id)

    detailed = await integration_client.get(
        f"/api/v1/calendar/events/{event.id}",
        headers=stranger_headers,
    )
    listed = await integration_client.get(
        "/api/v1/calendar/events",
        headers=stranger_headers,
    )

    assert detailed.status_code == 404
    assert detailed.json()["error"]["code"] == "CALENDAR_EVENT_NOT_FOUND"
    assert listed.json()["data"]["total"] == 0


async def test_calendar_availability_rejects_invalid_range(
    integration_session: AsyncSession,
    integration_client: AsyncClient,
) -> None:
    user = await UserService(integration_session).create_user(
        _user_create("calendar-invalid-api@example.com")
    )

    response = await integration_client.get(
        "/api/v1/calendar/availability",
        params={
            "start_at": "2026-07-20T11:00:00+08:00",
            "end_at": "2026-07-20T10:00:00+08:00",
        },
        headers=_auth_headers(user.id),
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "INVALID_CALENDAR_RANGE"
