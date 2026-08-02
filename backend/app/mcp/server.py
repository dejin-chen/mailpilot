"""FastMCP Streamable HTTP 应用组装。"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from mcp.server.fastmcp import FastMCP
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from app.core.config import Settings
from app.db.redis import close_redis
from app.mcp.security import InternalMcpAuthMiddleware


def build_mcp_http_app(mcp: FastMCP, *, settings: Settings, service_name: str):
    """为 FastMCP 增加健康检查和内部令牌认证中间件。"""

    if settings.mcp_internal_token is None:
        msg = "启动 MCP Server 前必须配置 MCP_INTERNAL_TOKEN"
        raise RuntimeError(msg)
    internal_token = settings.mcp_internal_token.get_secret_value()

    async def health(_: Request) -> JSONResponse:
        return JSONResponse({"status": "ok", "service": service_name})

    app = mcp.streamable_http_app()
    original_lifespan = app.router.lifespan_context

    @asynccontextmanager
    async def lifespan(application: Starlette) -> AsyncIterator[None]:
        """保留 FastMCP 生命周期，并在服务停止时关闭当前 Redis 连接池。"""

        try:
            async with original_lifespan(application):
                yield
        finally:
            await close_redis()

    app.router.lifespan_context = lifespan
    app.router.routes.append(Route("/health", health, methods=["GET"]))
    app.add_middleware(InternalMcpAuthMiddleware, internal_token=internal_token)
    return app
