"""健康检查响应 Schema。"""

from typing import Literal

from pydantic import BaseModel


class DependencyHealth(BaseModel):
    """单个基础设施依赖的状态。"""

    status: Literal["up", "down"]
    required: bool = True


class HealthData(BaseModel):
    """服务存活或就绪状态。"""

    status: Literal["ok", "degraded"]
    service: str
    version: str
    environment: str
    checks: dict[str, DependencyHealth] = {}


class VersionData(BaseModel):
    """服务版本信息。"""

    app_name: str
    app_version: str
    environment: str
