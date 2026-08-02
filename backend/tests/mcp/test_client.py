"""LangChain MCP Client 白名单、headers、超时与重试测试。"""

import asyncio
from typing import Any
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from app.core.config import Settings
from app.integrations.mcp.client import MailPilotMcpClient
from app.integrations.mcp.exceptions import (
    McpConfigurationError,
    McpToolInvocationError,
    McpToolNotAllowedError,
)


class FakeTool:
    """只实现 Client 测试需要的 LangChain Tool 接口。"""

    def __init__(self, name: str) -> None:
        self.name = name
        self.ainvoke = AsyncMock(return_value={"success": True})


class FakeAdapterClient:
    """返回预设工具的 Adapter 替身。"""

    def __init__(self, tools: list[FakeTool]) -> None:
        self._tools = tools

    async def get_tools(self, *, server_name: str | None = None) -> list[Any]:
        del server_name
        return self._tools


def _settings(**changes: object) -> Settings:
    values: dict[str, object] = {
        "_env_file": None,
        "database_url": "postgresql+psycopg://user:password@localhost:5432/mailpilot",
        "redis_url": "redis://localhost:6379/0",
        "jwt_secret_key": "test-jwt-secret-key-at-least-32-characters",
        "mcp_internal_token": "test-mcp-internal-token-at-least-32-characters",
        "mcp_timeout_seconds": 1,
        "mcp_max_retries": 1,
    }
    values.update(changes)
    return Settings(**values)  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_discover_tools_filters_allowlist_and_uses_hidden_headers() -> None:
    safe = FakeTool("search_emails")
    dangerous = FakeTool("send_email")
    captured: dict[str, dict[str, Any]] = {}

    def factory(connections: dict[str, dict[str, Any]]) -> FakeAdapterClient:
        captured.update(connections)
        return FakeAdapterClient([safe, dangerous])

    user_id = uuid4()
    client = MailPilotMcpClient(
        settings=_settings(),
        user_id=user_id,
        request_id="mcp-client-001",
        allowed_tools=frozenset({"search_emails"}),
        client_factory=factory,
    )

    tools = await client.discover_tools()

    assert [tool.name for tool in tools] == ["search_emails"]
    assert captured["mail"]["transport"] == "streamable_http"
    assert captured["mail"]["headers"]["X-MailPilot-User-ID"] == str(user_id)
    assert captured["mail"]["headers"]["X-Request-ID"] == "mcp-client-001"
    assert "user_id" not in safe.ainvoke.call_args_list


@pytest.mark.asyncio
async def test_client_rejects_tool_before_network_discovery() -> None:
    factory = AsyncMock()
    client = MailPilotMcpClient(
        settings=_settings(),
        user_id=uuid4(),
        request_id="mcp-client-002",
        allowed_tools=frozenset({"search_emails"}),
        client_factory=factory,
    )

    with pytest.raises(McpToolNotAllowedError):
        await client.invoke_tool("send_email", {})

    factory.assert_not_called()


@pytest.mark.asyncio
async def test_client_retries_idempotent_tool_once() -> None:
    tool = FakeTool("search_emails")
    tool.ainvoke.side_effect = [OSError("temporary"), {"total": 1}]
    client = MailPilotMcpClient(
        settings=_settings(),
        user_id=uuid4(),
        request_id="mcp-client-003",
        allowed_tools=frozenset({"search_emails"}),
        client_factory=lambda _: FakeAdapterClient([tool]),
    )

    result = await client.invoke_tool("search_emails", {"query": "周会"})

    assert result == {"total": 1}
    assert tool.ainvoke.await_count == 2


@pytest.mark.asyncio
async def test_client_never_retries_external_side_effect_tool() -> None:
    tool = FakeTool("send_email")
    tool.ainvoke.side_effect = OSError("failed")
    client = MailPilotMcpClient(
        settings=_settings(mcp_max_retries=3),
        user_id=uuid4(),
        request_id="mcp-client-004",
        allowed_tools=frozenset({"send_email"}),
        client_factory=lambda _: FakeAdapterClient([tool]),
    )

    with pytest.raises(McpToolInvocationError):
        await client.invoke_tool("send_email", {"draft_message_id": str(uuid4())})

    assert tool.ainvoke.await_count == 1


@pytest.mark.asyncio
async def test_client_timeout_becomes_stable_invocation_error() -> None:
    tool = FakeTool("search_emails")

    async def slow_call(_: dict[str, Any]) -> None:
        await asyncio.sleep(0.1)

    tool.ainvoke.side_effect = slow_call
    client = MailPilotMcpClient(
        settings=_settings(mcp_timeout_seconds=0.01),
        user_id=uuid4(),
        request_id="mcp-client-005",
        allowed_tools=frozenset({"search_emails"}),
        client_factory=lambda _: FakeAdapterClient([tool]),
    )

    with pytest.raises(McpToolInvocationError):
        await client.invoke_tool("search_emails", {})

    assert tool.ainvoke.await_count == 2


@pytest.mark.asyncio
async def test_client_does_not_retry_permanent_programming_error() -> None:
    tool = FakeTool("search_emails")
    tool.ainvoke.side_effect = ValueError("invalid arguments")
    client = MailPilotMcpClient(
        settings=_settings(mcp_max_retries=3),
        user_id=uuid4(),
        request_id="mcp-client-permanent-error",
        allowed_tools=frozenset({"search_emails"}),
        client_factory=lambda _: FakeAdapterClient([tool]),
    )

    with pytest.raises(McpToolInvocationError):
        await client.invoke_tool("search_emails", {})

    assert tool.ainvoke.await_count == 1


def test_client_requires_internal_token() -> None:
    with pytest.raises(McpConfigurationError):
        MailPilotMcpClient(
            settings=_settings(mcp_internal_token=None),
            user_id=uuid4(),
            request_id="mcp-client-006",
        )
