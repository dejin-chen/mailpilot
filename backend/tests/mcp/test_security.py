"""MCP 内部身份中间件测试。"""

from uuid import uuid4

import pytest
from app.mcp.security import InternalMcpAuthMiddleware, get_mcp_identity
from httpx import ASGITransport, AsyncClient
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

INTERNAL_TOKEN = "test-mcp-internal-token-at-least-32-characters"


async def identity_endpoint(_: Request) -> JSONResponse:
    identity = get_mcp_identity()
    return JSONResponse({"user_id": str(identity.user_id), "request_id": identity.request_id})


async def health_endpoint(_: Request) -> JSONResponse:
    return JSONResponse({"status": "ok"})


def _test_app() -> InternalMcpAuthMiddleware:
    inner = Starlette(
        routes=[
            Route("/mcp", identity_endpoint),
            Route("/health", health_endpoint),
        ]
    )
    return InternalMcpAuthMiddleware(inner, internal_token=INTERNAL_TOKEN)


@pytest.mark.asyncio
async def test_mcp_auth_accepts_internal_token_and_runtime_user() -> None:
    user_id = uuid4()
    transport = ASGITransport(app=_test_app())
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(
            "/mcp",
            headers={
                "Authorization": f"Bearer {INTERNAL_TOKEN}",
                "X-MailPilot-User-ID": str(user_id),
                "X-Request-ID": "mcp-security-001",
            },
        )

    assert response.status_code == 200
    assert response.json() == {
        "user_id": str(user_id),
        "request_id": "mcp-security-001",
    }


@pytest.mark.asyncio
async def test_mcp_auth_rejects_missing_token_without_leaking_details() -> None:
    transport = ASGITransport(app=_test_app())
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(
            "/mcp",
            headers={"X-MailPilot-User-ID": str(uuid4())},
        )

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "MCP_UNAUTHORIZED"
    assert INTERNAL_TOKEN not in response.text


@pytest.mark.asyncio
async def test_mcp_auth_rejects_invalid_user_id() -> None:
    transport = ASGITransport(app=_test_app())
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(
            "/mcp",
            headers={
                "Authorization": f"Bearer {INTERNAL_TOKEN}",
                "X-MailPilot-User-ID": "not-a-uuid",
            },
        )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "MCP_USER_ID_INVALID"


@pytest.mark.asyncio
async def test_mcp_health_does_not_require_internal_identity() -> None:
    transport = ASGITransport(app=_test_app())
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
