"""健康检查接口测试。"""

from collections.abc import Awaitable, Callable

import pytest
from app.api.v1 import health
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_live_returns_service_metadata(client: AsyncClient) -> None:
    response = await client.get("/api/v1/health/live")

    assert response.status_code == 200
    payload = response.json()
    assert payload["success"] is True
    assert payload["data"]["status"] == "ok"
    assert payload["data"]["service"] == "MailPilot"
    assert payload["request_id"] == response.headers["X-Request-ID"]


@pytest.mark.asyncio
async def test_live_reuses_valid_request_id(client: AsyncClient) -> None:
    response = await client.get(
        "/api/v1/health/live", headers={"X-Request-ID": "health-check-001"}
    )

    assert response.status_code == 200
    assert response.headers["X-Request-ID"] == "health-check-001"
    assert response.json()["request_id"] == "health-check-001"


@pytest.mark.asyncio
async def test_version_returns_safe_service_metadata(client: AsyncClient) -> None:
    response = await client.get(
        "/api/v1/health/version",
        headers={"X-Request-ID": "version-demo-001"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["success"] is True
    assert payload["data"] == {
        "app_name": "MailPilot",
        "app_version": "0.1.0",
        "environment": "test",
    }
    assert payload["request_id"] == "version-demo-001"
    assert response.headers["X-Request-ID"] == "version-demo-001"


def _async_result(value: bool) -> Callable[[], Awaitable[bool]]:
    async def result() -> bool:
        return value

    return result


@pytest.mark.asyncio
async def test_ready_returns_ok_when_dependencies_are_available(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(health, "check_database", _async_result(True))
    monkeypatch.setattr(health, "check_redis", _async_result(True))

    response = await client.get("/api/v1/health/ready")

    assert response.status_code == 200
    payload = response.json()
    assert payload["success"] is True
    assert payload["data"]["checks"]["postgresql"]["status"] == "up"
    assert payload["data"]["checks"]["redis"]["status"] == "up"


@pytest.mark.asyncio
async def test_ready_returns_503_when_database_is_unavailable(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(health, "check_database", _async_result(False))
    monkeypatch.setattr(health, "check_redis", _async_result(True))

    response = await client.get("/api/v1/health/ready")

    assert response.status_code == 503
    payload = response.json()
    assert payload["success"] is False
    assert payload["data"]["status"] == "degraded"
    assert payload["data"]["checks"]["postgresql"]["status"] == "down"
