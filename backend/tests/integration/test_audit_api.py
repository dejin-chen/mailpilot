"""管理员审计查询 API 集成测试。"""

from uuid import uuid4

import pytest
from app.models.audit import AuditLog
from app.models.user import UserRole
from app.schemas.user import UserCreate
from app.security.jwt import create_access_token
from app.services.user import UserService
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = pytest.mark.integration


def _headers(user_id: object) -> dict[str, str]:
    return {"Authorization": f"Bearer {create_access_token(user_id)}"}


async def test_only_admin_can_query_and_filter_audit_logs(
    integration_session: AsyncSession,
    integration_client: AsyncClient,
) -> None:
    suffix = uuid4().hex
    owner = await UserService(integration_session).create_user(
        UserCreate(
            email=f"audit-owner-{suffix}@example.com",
            password="audit-owner-password",
            full_name="审计数据所有者",
            timezone="Asia/Shanghai",
        )
    )
    admin = await UserService(integration_session).create_user(
        UserCreate(
            email=f"audit-admin-{suffix}@example.com",
            password="audit-admin-password",
            full_name="审计管理员",
            timezone="Asia/Shanghai",
        )
    )
    admin.role = UserRole.ADMIN
    integration_session.add_all(
        [
            AuditLog(
                user_id=owner.id,
                actor_user_id=owner.id,
                action="security.test_event",
                resource_type="user",
                resource_id=owner.id,
                request_id="audit-api-001",
                details={"safe": True},
            ),
            AuditLog(
                user_id=owner.id,
                actor_user_id=owner.id,
                action="security.other_event",
                resource_type="user",
                resource_id=owner.id,
                details={},
            ),
        ]
    )
    await integration_session.commit()

    unauthenticated = await integration_client.get("/api/v1/audit-logs")
    regular_user = await integration_client.get(
        "/api/v1/audit-logs",
        headers=_headers(owner.id),
    )
    filtered = await integration_client.get(
        "/api/v1/audit-logs",
        params={"user_id": str(owner.id), "action": "security.test_event"},
        headers=_headers(admin.id),
    )

    assert unauthenticated.status_code == 401
    assert regular_user.status_code == 403
    assert regular_user.json()["error"]["code"] == "ADMINISTRATOR_REQUIRED"
    assert filtered.status_code == 200
    payload = filtered.json()["data"]
    assert payload["total"] == 1
    assert payload["items"][0]["user_id"] == str(owner.id)
    assert payload["items"][0]["action"] == "security.test_event"
    assert payload["items"][0]["details"] == {"safe": True}
