"""登录和当前用户 API 集成测试。"""

from uuid import uuid4

import pytest
from app.main import app
from app.models.audit import AuditLog
from app.schemas.user import UserCreate
from app.security.exceptions import LoginRateLimitExceededError
from app.security.jwt import create_access_token
from app.security.rate_limit import get_login_rate_limiter
from app.services.user import UserService
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = pytest.mark.integration


def _user_create() -> UserCreate:
    return UserCreate(
        email="api-user@example.com",
        password="api-integration-password",
        full_name="API 集成用户",
        timezone="Asia/Shanghai",
    )


async def test_login_then_get_current_user(
    integration_session: AsyncSession,
    integration_client: AsyncClient,
) -> None:
    user = await UserService(integration_session).create_user(_user_create())

    login_response = await integration_client.post(
        "/api/v1/auth/login",
        json={"email": " API-USER@Example.com ", "password": "api-integration-password"},
        headers={"X-Request-ID": "login-flow-001"},
    )

    assert login_response.status_code == 200
    login_payload = login_response.json()
    assert login_payload["success"] is True
    assert login_payload["data"]["token_type"] == "bearer"
    assert login_payload["data"]["expires_in"] == 1800
    assert login_payload["request_id"] == "login-flow-001"

    me_response = await integration_client.get(
        "/api/v1/users/me",
        headers={
            "Authorization": f"Bearer {login_payload['data']['access_token']}",
            "X-Request-ID": "current-user-001",
        },
    )

    assert me_response.status_code == 200
    me_payload = me_response.json()
    assert me_payload["data"]["email"] == "api-user@example.com"
    assert me_payload["data"]["role"] == "user"
    assert me_payload["request_id"] == "current-user-001"
    assert "password" not in me_payload["data"]
    assert "password_hash" not in me_payload["data"]
    audit = await integration_session.scalar(
        select(AuditLog).where(
            AuditLog.user_id == user.id,
            AuditLog.action == "auth.login_succeeded",
        )
    )
    assert audit is not None
    assert audit.request_id == "login-flow-001"


async def test_login_rejects_wrong_password_with_bearer_challenge(
    integration_session: AsyncSession,
    integration_client: AsyncClient,
) -> None:
    await UserService(integration_session).create_user(_user_create())

    response = await integration_client.post(
        "/api/v1/auth/login",
        json={"email": "api-user@example.com", "password": "wrong-password"},
    )

    assert response.status_code == 401
    assert response.headers["WWW-Authenticate"] == "Bearer"
    assert response.json()["error"]["code"] == "INVALID_CREDENTIALS"


async def test_current_user_requires_token(integration_client: AsyncClient) -> None:
    response = await integration_client.get("/api/v1/users/me")

    assert response.status_code == 401
    assert response.headers["WWW-Authenticate"] == "Bearer"
    assert response.json()["error"]["code"] == "AUTHENTICATION_REQUIRED"


async def test_current_user_rejects_tampered_token(integration_client: AsyncClient) -> None:
    token = create_access_token(uuid4())
    header, payload, signature = token.split(".")
    replacement = "A" if signature[0] != "A" else "B"
    tampered_token = ".".join((header, payload, replacement + signature[1:]))

    response = await integration_client.get(
        "/api/v1/users/me",
        headers={"Authorization": f"Bearer {tampered_token}"},
    )

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "INVALID_ACCESS_TOKEN"


async def test_current_user_rechecks_inactive_status(
    integration_session: AsyncSession,
    integration_client: AsyncClient,
) -> None:
    user = await UserService(integration_session).create_user(_user_create())
    token = create_access_token(user.id)
    user.is_active = False
    await integration_session.commit()

    response = await integration_client.get(
        "/api/v1/users/me",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "USER_INACTIVE"


async def test_login_endpoint_returns_429_before_password_check(
    integration_client: AsyncClient,
) -> None:
    class RejectingLimiter:
        async def check(self, **_: object) -> None:
            raise LoginRateLimitExceededError(23)

    app.dependency_overrides[get_login_rate_limiter] = lambda: RejectingLimiter()
    try:
        response = await integration_client.post(
            "/api/v1/auth/login",
            json={"email": "limited@example.com", "password": "not-checked"},
        )
    finally:
        app.dependency_overrides.pop(get_login_rate_limiter, None)

    assert response.status_code == 429
    assert response.headers["Retry-After"] == "23"
    assert response.json()["error"]["code"] == "LOGIN_RATE_LIMIT_EXCEEDED"
