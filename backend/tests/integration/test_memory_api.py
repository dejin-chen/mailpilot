"""长期记忆 API、版本、审计、删除和用户隔离集成测试。"""

from uuid import UUID

import pytest
from app.models.audit import AuditLog
from app.models.memory import MemoryProfileVersion
from app.repositories.audit import AuditRepository
from app.schemas.user import UserCreate
from app.security.jwt import create_access_token
from app.services.user import UserService
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = pytest.mark.integration


def _user_create(email: str) -> UserCreate:
    return UserCreate(
        email=email,
        password="memory-api-password",
        full_name="长期记忆测试用户",
        timezone="Asia/Shanghai",
    )


def _auth_headers(user_id: UUID, request_id: str | None = None) -> dict[str, str]:
    headers = {"Authorization": f"Bearer {create_access_token(user_id)}"}
    if request_id is not None:
        headers["X-Request-ID"] = request_id
    return headers


async def test_memory_create_list_get_update_and_audit(
    integration_session: AsyncSession,
    integration_client: AsyncClient,
) -> None:
    user = await UserService(integration_session).create_user(
        _user_create("memory-flow@example.com")
    )
    headers = _auth_headers(user.id, "memory-flow-request")

    created = await integration_client.post(
        "/api/v1/memories",
        json={
            "memory_type": "email_style",
            "memory_key": "DEFAULT",
            "value": {
                "tone": "简洁专业",
                "signature": "张三",
            },
        },
        headers=headers,
    )
    assert created.status_code == 201
    created_data = created.json()["data"]
    memory_id = created_data["id"]
    assert created_data["memory_key"] == "default"
    assert created_data["version"] == 1
    assert created_data["source_type"] == "user_api_edit"

    listed = await integration_client.get(
        "/api/v1/memories",
        params={"memory_type": "email_style"},
        headers=headers,
    )
    detailed = await integration_client.get(
        f"/api/v1/memories/{memory_id}",
        headers=headers,
    )
    updated = await integration_client.put(
        f"/api/v1/memories/{memory_id}",
        json={
            "memory_type": "email_style",
            "expected_version": 1,
            "value": {
                "tone": "友好且简洁",
                "signature": "张三｜研发部",
            },
        },
        headers=headers,
    )
    versions = await integration_client.get(
        f"/api/v1/memories/{memory_id}/versions",
        headers=headers,
    )

    assert listed.status_code == 200
    assert listed.json()["data"]["total"] == 1
    assert detailed.status_code == 200
    assert detailed.json()["data"]["value"]["tone"] == "简洁专业"
    assert updated.status_code == 200
    assert updated.json()["data"]["version"] == 2
    assert updated.json()["data"]["value"]["tone"] == "友好且简洁"
    assert versions.status_code == 200
    version_data = versions.json()["data"]
    assert version_data["total"] == 2
    assert [item["version"] for item in version_data["items"]] == [2, 1]
    assert version_data["items"][0]["value"]["tone"] == "友好且简洁"
    assert version_data["items"][1]["value"]["tone"] == "简洁专业"
    assert all(item["source_type"] == "user_api_edit" for item in version_data["items"])

    version_count = await integration_session.scalar(
        select(func.count())
        .select_from(MemoryProfileVersion)
        .where(MemoryProfileVersion.memory_profile_id == UUID(memory_id))
    )
    assert version_count == 2

    events = await AuditRepository(integration_session).list_events(user_id=user.id)
    actions = {event.action for event in events}
    assert {
        "memory.created",
        "memory.listed",
        "memory.read",
        "memory.updated",
        "memory.versions_listed",
    }.issubset(actions)
    memory_events = [event for event in events if event.action.startswith("memory.")]
    assert all(event.request_id == "memory-flow-request" for event in memory_events)
    assert all("value" not in event.details for event in memory_events)
    assert all("简洁专业" not in str(event.details) for event in memory_events)


async def test_memory_rejects_duplicate_and_stale_version(
    integration_session: AsyncSession,
    integration_client: AsyncClient,
) -> None:
    user = await UserService(integration_session).create_user(
        _user_create("memory-conflict@example.com")
    )
    headers = _auth_headers(user.id)
    payload = {
        "memory_type": "calendar_preferences",
        "value": {
            "timezone": "Asia/Shanghai",
            "default_duration_minutes": 30,
        },
    }
    created = await integration_client.post(
        "/api/v1/memories",
        json=payload,
        headers=headers,
    )
    memory_id = created.json()["data"]["id"]

    duplicate = await integration_client.post(
        "/api/v1/memories",
        json=payload,
        headers=headers,
    )
    first_update = await integration_client.put(
        f"/api/v1/memories/{memory_id}",
        json={
            "memory_type": "calendar_preferences",
            "expected_version": 1,
            "value": {
                "timezone": "Asia/Shanghai",
                "default_duration_minutes": 45,
            },
        },
        headers=headers,
    )
    stale_update = await integration_client.put(
        f"/api/v1/memories/{memory_id}",
        json={
            "memory_type": "calendar_preferences",
            "expected_version": 1,
            "value": {
                "timezone": "Asia/Shanghai",
                "default_duration_minutes": 60,
            },
        },
        headers=headers,
    )

    assert duplicate.status_code == 409
    assert duplicate.json()["error"]["code"] == "MEMORY_PROFILE_CONFLICT"
    assert first_update.status_code == 200
    assert stale_update.status_code == 409
    assert stale_update.json()["error"]["code"] == "MEMORY_PROFILE_VERSION_CONFLICT"
    assert stale_update.json()["error"]["details"]["current_version"] == 2


async def test_memory_user_isolation_and_delete_cascades_versions(
    integration_session: AsyncSession,
    integration_client: AsyncClient,
) -> None:
    owner = await UserService(integration_session).create_user(
        _user_create("memory-owner@example.com")
    )
    stranger = await UserService(integration_session).create_user(
        _user_create("memory-stranger@example.com")
    )
    owner_headers = _auth_headers(owner.id)
    stranger_headers = _auth_headers(stranger.id)
    created = await integration_client.post(
        "/api/v1/memories",
        json={
            "memory_type": "contact",
            "memory_key": "manager@example.com",
            "value": {
                "email": "manager@example.com",
                "display_name": "王经理",
                "salutation": "王经理，您好",
                "important": True,
            },
        },
        headers=owner_headers,
    )
    memory_id = created.json()["data"]["id"]

    stranger_list = await integration_client.get(
        "/api/v1/memories",
        headers=stranger_headers,
    )
    stranger_get = await integration_client.get(
        f"/api/v1/memories/{memory_id}",
        headers=stranger_headers,
    )
    stranger_update = await integration_client.put(
        f"/api/v1/memories/{memory_id}",
        json={
            "memory_type": "contact",
            "expected_version": 1,
            "value": {
                "email": "manager@example.com",
                "display_name": "恶意修改",
            },
        },
        headers=stranger_headers,
    )
    stranger_delete = await integration_client.delete(
        f"/api/v1/memories/{memory_id}",
        headers=stranger_headers,
    )
    stranger_versions = await integration_client.get(
        f"/api/v1/memories/{memory_id}/versions",
        headers=stranger_headers,
    )

    assert stranger_list.status_code == 200
    assert stranger_list.json()["data"]["total"] == 0
    assert stranger_get.status_code == 404
    assert stranger_update.status_code == 404
    assert stranger_delete.status_code == 404
    assert stranger_versions.status_code == 404

    deleted = await integration_client.delete(
        f"/api/v1/memories/{memory_id}",
        headers=owner_headers,
    )
    missing = await integration_client.get(
        f"/api/v1/memories/{memory_id}",
        headers=owner_headers,
    )
    assert deleted.status_code == 200
    assert deleted.json()["data"]["deleted"] is True
    assert missing.status_code == 404

    version_count = await integration_session.scalar(
        select(func.count())
        .select_from(MemoryProfileVersion)
        .where(MemoryProfileVersion.memory_profile_id == UUID(memory_id))
    )
    assert version_count == 0
    delete_event = await integration_session.scalar(
        select(AuditLog).where(
            AuditLog.user_id == owner.id,
            AuditLog.action == "memory.deleted",
            AuditLog.resource_id == UUID(memory_id),
        )
    )
    assert delete_event is not None
    assert delete_event.details["memory_key"] == "manager@example.com"
    assert "value" not in delete_event.details


async def test_memory_rejects_contact_key_mismatch(
    integration_session: AsyncSession,
    integration_client: AsyncClient,
) -> None:
    user = await UserService(integration_session).create_user(
        _user_create("memory-invalid@example.com")
    )
    response = await integration_client.post(
        "/api/v1/memories",
        json={
            "memory_type": "contact",
            "memory_key": "wrong@example.com",
            "value": {
                "email": "manager@example.com",
                "display_name": "王经理",
            },
        },
        headers=_auth_headers(user.id),
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "MEMORY_PROFILE_VALUE_INVALID"
