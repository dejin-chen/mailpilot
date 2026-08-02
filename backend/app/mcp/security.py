"""MCP Streamable HTTP 内部身份认证。"""

from __future__ import annotations

from contextvars import ContextVar, Token
from dataclasses import dataclass
from hmac import compare_digest
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

from starlette.responses import JSONResponse

if TYPE_CHECKING:
    from starlette.types import ASGIApp, Receive, Scope, Send


@dataclass(frozen=True, slots=True)
class McpIdentity:
    """经过内部令牌认证后，本次 MCP 调用使用的可信身份。"""

    user_id: UUID
    request_id: str


mcp_identity_context: ContextVar[McpIdentity | None] = ContextVar(
    "mcp_identity",
    default=None,
)


class InternalMcpAuthMiddleware:
    """验证内部 Bearer Token，并把用户身份放入不可见的运行时上下文。"""

    def __init__(self, app: ASGIApp, *, internal_token: str) -> None:
        if len(internal_token) < 32:
            msg = "MCP 内部令牌未配置或长度不足"
            raise RuntimeError(msg)
        self._app = app
        self._internal_token = internal_token

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope.get("path") == "/health":
            await self._app(scope, receive, send)
            return

        headers = {
            key.decode("latin-1").lower(): value.decode("latin-1")
            for key, value in scope.get("headers", [])
        }
        authorization = headers.get("authorization", "")
        expected = f"Bearer {self._internal_token}"
        if not compare_digest(authorization, expected):
            await self._reject(scope, receive, send, status_code=401, code="MCP_UNAUTHORIZED")
            return

        raw_user_id = headers.get("x-mailpilot-user-id", "")
        try:
            user_id = UUID(raw_user_id)
        except ValueError:
            await self._reject(
                scope,
                receive,
                send,
                status_code=400,
                code="MCP_USER_ID_INVALID",
            )
            return

        identity = McpIdentity(
            user_id=user_id,
            request_id=headers.get("x-request-id") or str(uuid4()),
        )
        context_token: Token[McpIdentity | None] = mcp_identity_context.set(identity)
        try:
            await self._app(scope, receive, send)
        finally:
            mcp_identity_context.reset(context_token)

    @staticmethod
    async def _reject(
        scope: Scope,
        receive: Receive,
        send: Send,
        *,
        status_code: int,
        code: str,
    ) -> None:
        """返回不包含令牌、用户信息或内部实现细节的认证错误。"""

        response = JSONResponse(
            status_code=status_code,
            content={"success": False, "error": {"code": code, "message": "MCP 请求认证失败"}},
        )
        await response(scope, receive, send)


def get_mcp_identity() -> McpIdentity:
    """读取中间件建立的可信调用身份。"""

    identity = mcp_identity_context.get()
    if identity is None:
        msg = "当前调用缺少 MCP 身份上下文"
        raise RuntimeError(msg)
    return identity
