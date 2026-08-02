"""带身份、白名单、超时和有限重试的 LangChain MCP Client。"""

import asyncio
import logging
from collections.abc import Callable
from typing import Any, Protocol
from uuid import UUID

import httpx
from langchain_core.tools import BaseTool
from langchain_mcp_adapters.client import MultiServerMCPClient

from app.core.config import Settings
from app.integrations.mcp.exceptions import (
    McpConfigurationError,
    McpToolInvocationError,
    McpToolNotAllowedError,
    McpToolUnavailableError,
)
from app.observability.base import Observability
from app.observability.noop import NoOpObservability

logger = logging.getLogger(__name__)

READ_ONLY_TOOL_ALLOWLIST = frozenset(
    {
        "get_email_thread",
        "search_emails",
        "check_availability",
        "find_available_slots",
    }
)
LOCAL_IDEMPOTENT_TOOL_ALLOWLIST = frozenset({"create_email_draft", "mark_email_processed"})
SAFE_AGENT_TOOL_ALLOWLIST = READ_ONLY_TOOL_ALLOWLIST | LOCAL_IDEMPOTENT_TOOL_ALLOWLIST
APPROVAL_REQUIRED_TOOLS = frozenset(
    {"send_email", "create_event", "reschedule_event", "cancel_event"}
)
RETRYABLE_TOOLS = SAFE_AGENT_TOOL_ALLOWLIST
TOOL_SERVER: dict[str, str] = {
    "get_email_thread": "mail",
    "search_emails": "mail",
    "create_email_draft": "mail",
    "send_email": "mail",
    "mark_email_processed": "mail",
    "check_availability": "calendar",
    "find_available_slots": "calendar",
    "create_event": "calendar",
    "reschedule_event": "calendar",
    "cancel_event": "calendar",
}


class McpAdapterClient(Protocol):
    """本项目实际使用的 LangChain Adapter 最小接口。"""

    async def get_tools(self, *, server_name: str | None = None) -> list[BaseTool]: ...


ClientFactory = Callable[[dict[str, dict[str, Any]]], McpAdapterClient]


class MailPilotMcpClient:
    """按用户创建短期 MCP Client，并在调用前执行显式工具授权。"""

    def __init__(
        self,
        *,
        settings: Settings,
        user_id: UUID,
        request_id: str,
        allowed_tools: frozenset[str] = SAFE_AGENT_TOOL_ALLOWLIST,
        client_factory: ClientFactory = MultiServerMCPClient,
        observability: Observability | None = None,
    ) -> None:
        if settings.mcp_internal_token is None:
            raise McpConfigurationError
        self._settings = settings
        self._user_id = user_id
        self._request_id = request_id
        self._allowed_tools = allowed_tools
        self._client_factory = client_factory
        self._observability = observability or NoOpObservability()

    async def discover_tools(self) -> list[BaseTool]:
        """从两个 Server 发现工具，并只返回当前允许列表中的工具。"""

        client = self._new_adapter_client()
        try:
            async with asyncio.timeout(self._settings.mcp_timeout_seconds):
                discovered = []
                server_names = sorted(
                    {TOOL_SERVER[name] for name in self._allowed_tools if name in TOOL_SERVER}
                )
                for server_name in server_names:
                    discovered.extend(await client.get_tools(server_name=server_name))
        except Exception as exc:
            logger.warning(
                "MCP 工具发现失败",
                extra={"request_id": self._request_id, "error_type": type(exc).__name__},
            )
            raise McpToolInvocationError("tool_discovery") from exc

        tools_by_name = {tool.name: tool for tool in discovered}
        missing = self._allowed_tools - tools_by_name.keys()
        if missing:
            raise McpToolUnavailableError(sorted(missing)[0])
        return [tools_by_name[name] for name in sorted(self._allowed_tools)]

    async def invoke_tool(self, tool_name: str, arguments: dict[str, Any]) -> Any:
        """调用一个已授权工具；只对幂等工具执行有限重试。"""

        if tool_name not in self._allowed_tools:
            raise McpToolNotAllowedError(tool_name)

        with self._observability.span(
            name=f"mcp.{tool_name}",
            input=arguments,
            metadata={
                "tool_name": tool_name,
                "server": TOOL_SERVER.get(tool_name, "unknown"),
                "request_id": self._request_id,
                "read_only": tool_name in READ_ONLY_TOOL_ALLOWLIST,
            },
        ) as tool_span:
            attempts = 1 + (self._settings.mcp_max_retries if tool_name in RETRYABLE_TOOLS else 0)
            last_error: Exception | None = None
            for attempt in range(attempts):
                try:
                    tool = await self._get_allowed_tool(tool_name)
                    async with asyncio.timeout(self._settings.mcp_timeout_seconds):
                        result = await tool.ainvoke(arguments)
                    tool_span.update(
                        output=result,
                        metadata={"success": True, "attempt_count": attempt + 1},
                    )
                    return result
                except Exception as exc:
                    last_error = exc
                    logger.warning(
                        "MCP 工具调用失败",
                        extra={
                            "request_id": self._request_id,
                            "tool_name": tool_name,
                            "attempt": attempt + 1,
                            "error_type": type(exc).__name__,
                        },
                    )
                    if attempt + 1 < attempts and _is_transient_error(exc):
                        await asyncio.sleep(0.1 * (attempt + 1))
                        continue
                    tool_span.update(
                        level="ERROR",
                        status_message="MCP 工具调用失败",
                        metadata={
                            "success": False,
                            "attempt_count": attempt + 1,
                            "error_type": type(exc).__name__,
                        },
                    )
                    break

            raise McpToolInvocationError(tool_name) from last_error

    async def _get_allowed_tool(self, tool_name: str) -> BaseTool:
        """重新发现工具，避免长期持有可能失效的 MCP Session。"""

        tools = await self.discover_tools()
        for tool in tools:
            if tool.name == tool_name:
                return tool
        raise McpToolUnavailableError(tool_name)

    def _new_adapter_client(self) -> McpAdapterClient:
        """构造 headers；内部 Token 和 user_id 不进入模型可见 Schema。"""

        token = self._settings.mcp_internal_token
        if token is None:
            raise McpConfigurationError
        headers = {
            "Authorization": f"Bearer {token.get_secret_value()}",
            "X-MailPilot-User-ID": str(self._user_id),
            "X-Request-ID": self._request_id,
        }
        timeout = self._settings.mcp_timeout_seconds
        return self._client_factory(
            {
                "mail": {
                    "transport": "streamable_http",
                    "url": self._settings.mail_mcp_url,
                    "headers": headers,
                    "timeout": timeout,
                    "sse_read_timeout": timeout,
                },
                "calendar": {
                    "transport": "streamable_http",
                    "url": self._settings.calendar_mcp_url,
                    "headers": headers,
                    "timeout": timeout,
                    "sse_read_timeout": timeout,
                },
            }
        )


def _is_transient_error(exc: BaseException) -> bool:
    """只对连接、传输和超时类瞬时错误开放自动重试。"""

    if isinstance(exc, (OSError, TimeoutError, httpx.TransportError)):
        return True
    if isinstance(exc, BaseExceptionGroup):
        return any(_is_transient_error(item) for item in exc.exceptions)
    return False
