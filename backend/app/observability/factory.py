"""根据配置构建 Langfuse 或无外部依赖的空实现。"""

import logging
from functools import lru_cache

from app.core.config import Settings, get_settings
from app.observability.base import Observability
from app.observability.langfuse_client import LangfuseObservability
from app.observability.noop import NoOpObservability

logger = logging.getLogger(__name__)


def build_observability(settings: Settings) -> Observability:
    """配置未完成或初始化失败时安全退化，不阻断核心邮件流程。"""

    if not settings.langfuse_enabled:
        return NoOpObservability(capture_content=settings.langfuse_capture_content)
    if settings.langfuse_public_key is None or settings.langfuse_secret_key is None:
        logger.warning("Langfuse 已启用但凭证不完整，已退化为 NoOp")
        return NoOpObservability(capture_content=settings.langfuse_capture_content)
    try:
        return LangfuseObservability(
            public_key=settings.langfuse_public_key,
            secret_key=settings.langfuse_secret_key.get_secret_value(),
            base_url=settings.langfuse_base_url,
            environment=settings.environment,
            release=settings.app_version,
            capture_content=settings.langfuse_capture_content,
        )
    except Exception:
        logger.warning("初始化 Langfuse 失败，已退化为 NoOp", exc_info=True)
        return NoOpObservability(capture_content=settings.langfuse_capture_content)


@lru_cache
def get_observability() -> Observability:
    """返回进程内共享观察客户端，避免重复创建后台导出线程。"""

    return build_observability(get_settings())
