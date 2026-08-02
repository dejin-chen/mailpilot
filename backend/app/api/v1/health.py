"""存活与就绪健康检查接口。"""

import asyncio

from fastapi import APIRouter, Response, status

from app.core.config import get_settings
from app.core.logging import request_id_context
from app.db.redis import check_redis
from app.db.session import check_database
from app.schemas.common import ApiResponse
from app.schemas.health import DependencyHealth, HealthData, VersionData

router = APIRouter(prefix="/health", tags=["健康检查"])
settings = get_settings()


@router.get(
    "/live",
    response_model=ApiResponse[HealthData],
    summary="服务存活检查",
)
async def live() -> ApiResponse[HealthData]:
    """只检查 API 进程是否能够响应，不访问外部依赖。"""

    return ApiResponse(
        success=True,
        data=HealthData(
            status="ok",
            service=settings.app_name,
            version=settings.app_version,
            environment=settings.environment,
        ),
        request_id=request_id_context.get(),
    )


@router.get(
    "/version",
    response_model=ApiResponse[VersionData],
    summary="服务版本信息",
)
async def version() -> ApiResponse[VersionData]:
    """返回不包含敏感配置的服务版本信息。"""

    return ApiResponse(
        success=True,
        data=VersionData(
            app_name=settings.app_name,
            app_version=settings.app_version,
            environment=settings.environment,
        ),
        request_id=request_id_context.get(),
    )


@router.get(
    "/ready",
    response_model=ApiResponse[HealthData],
    summary="服务就绪检查",
    responses={status.HTTP_503_SERVICE_UNAVAILABLE: {"description": "依赖服务不可用"}},
)
async def ready(response: Response) -> ApiResponse[HealthData]:
    """并行检查 PostgreSQL 和 Redis；任一失败都返回 503。"""

    database_ok, redis_ok = await asyncio.gather(check_database(), check_redis())
    ready_ok = database_ok and redis_ok
    if not ready_ok:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE

    return ApiResponse(
        success=ready_ok,
        data=HealthData(
            status="ok" if ready_ok else "degraded",
            service=settings.app_name,
            version=settings.app_version,
            environment=settings.environment,
            checks={
                "postgresql": DependencyHealth(status="up" if database_ok else "down"),
                "redis": DependencyHealth(status="up" if redis_ok else "down"),
            },
        ),
        request_id=request_id_context.get(),
    )
