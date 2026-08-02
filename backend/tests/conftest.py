"""测试环境和共享 Fixture。"""

import os
from collections.abc import AsyncIterator

import pytest
from httpx import ASGITransport, AsyncClient

os.environ.setdefault("ENVIRONMENT", "test")
os.environ.setdefault("LOG_JSON", "true")
os.environ.setdefault(
    "DATABASE_URL", "postgresql+psycopg://mailpilot:mailpilot_test@localhost:5432/mailpilot_test"
)
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/15")
os.environ.setdefault("LOGIN_RATE_LIMIT_ENABLED", "false")
os.environ.setdefault("JWT_SECRET_KEY", "test-only-jwt-secret-key-at-least-32-chars")
os.environ.setdefault("MCP_INTERNAL_TOKEN", "test-only-mcp-internal-token-at-least-32-chars")

from app.main import app  # noqa: E402


@pytest.fixture
async def client() -> AsyncIterator[AsyncClient]:
    """提供不依赖真实网络端口的 ASGI 测试客户端。"""

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as test_client:
        yield test_client
