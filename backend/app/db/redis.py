"""Redis 客户端与生命周期。"""

import asyncio
import logging
from weakref import WeakKeyDictionary

from redis.asyncio import Redis
from redis.exceptions import RedisError

from app.core.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()

_clients: WeakKeyDictionary[asyncio.AbstractEventLoop, Redis] = WeakKeyDictionary()


def get_redis_client() -> Redis:
    """返回当前事件循环共享的 Redis Client 与连接池。"""

    loop = asyncio.get_running_loop()
    client = _clients.get(loop)
    if client is None:
        client = Redis.from_url(
            settings.redis_url,
            encoding="utf-8",
            decode_responses=True,
            socket_connect_timeout=settings.redis_socket_timeout_seconds,
            socket_timeout=settings.redis_socket_timeout_seconds,
        )
        _clients[loop] = client
    return client


async def check_redis() -> bool:
    """发送 PING，确认 Redis 真正可用。"""

    try:
        return bool(await get_redis_client().ping())
    except (RedisError, OSError):
        logger.exception("Redis 健康检查失败")
        return False


async def close_redis() -> None:
    """关闭当前事件循环拥有的 Redis 连接池。"""

    loop = asyncio.get_running_loop()
    client = _clients.pop(loop, None)
    if client is not None:
        await client.aclose()
