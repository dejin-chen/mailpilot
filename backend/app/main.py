"""MailPilot FastAPI 应用入口。"""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.agent.checkpoint import open_postgres_checkpointer
from app.api.router import api_router
from app.core.config import get_settings
from app.core.exceptions import register_exception_handlers
from app.core.logging import RequestContextMiddleware, configure_logging, request_id_context
from app.db.redis import close_redis
from app.db.session import close_database
from app.memory.store import open_postgres_store
from app.observability.factory import get_observability
from app.schemas.common import ApiResponse

settings = get_settings()
configure_logging(settings.log_level, json_logs=settings.log_json)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(application: FastAPI) -> AsyncIterator[None]:
    """管理应用级资源的启动和释放。"""

    logger.info(
        "MailPilot API 启动",
        extra={"environment": settings.environment, "app_version": settings.app_version},
    )
    try:
        async with (
            open_postgres_checkpointer(settings) as checkpointer,
            open_postgres_store(settings) as store,
        ):
            application.state.agent_checkpointer = checkpointer
            application.state.agent_store = store
            yield
    finally:
        if hasattr(application.state, "agent_checkpointer"):
            del application.state.agent_checkpointer
        if hasattr(application.state, "agent_store"):
            del application.state.agent_store
        await close_redis()
        await close_database()
        get_observability().shutdown()
        logger.info("MailPilot API 已停止")


def create_app() -> FastAPI:
    """创建 FastAPI 实例，便于测试和未来按环境扩展。"""

    application = FastAPI(
        title=settings.app_name,
        version=settings.app_version,
        description="企业邮件与日程协同 Agent 后端接口",
        debug=settings.debug,
        lifespan=lifespan,
    )
    application.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    application.add_middleware(RequestContextMiddleware)
    application.include_router(api_router)
    register_exception_handlers(application)

    @application.get("/", response_model=ApiResponse[dict[str, str]], tags=["系统"])
    async def root() -> ApiResponse[dict[str, str]]:
        """返回服务基本信息。"""

        return ApiResponse(
            success=True,
            data={"name": settings.app_name, "version": settings.app_version},
            request_id=request_id_context.get(),
        )

    return application


app = create_app()
