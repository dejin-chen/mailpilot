"""外部写操作使用的 Redis 分布式锁。"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from hashlib import sha256
from typing import Protocol
from uuid import uuid4

from redis.exceptions import RedisError

from app.core.config import Settings, get_settings
from app.db.redis import get_redis_client
from app.services.exceptions import (
    ToolExecutionInProgressError,
    WriteSafetyControlUnavailableError,
)

logger = logging.getLogger(__name__)

_RELEASE_SCRIPT = """
if redis.call('GET', KEYS[1]) == ARGV[1] then
  return redis.call('DEL', KEYS[1])
end
return 0
""".strip()


class RedisLockClient(Protocol):
    """分布式锁依赖的最小 Redis 接口。"""

    async def set(
        self,
        name: str,
        value: str,
        *,
        nx: bool,
        ex: int,
    ) -> object: ...

    async def eval(
        self,
        script: str,
        numkeys: int,
        *keys_and_args: object,
    ) -> object: ...


class WriteLockManager(Protocol):
    """正式 Redis 锁和测试 Fake 共同遵循的接口。"""

    def hold(self, idempotency_key: str) -> AbstractAsyncContextManager[None]: ...


class RedisWriteLockManager:
    """用 SET NX EX 获取锁，并用随机令牌安全释放自己的锁。"""

    def __init__(
        self,
        *,
        client: RedisLockClient | None = None,
        settings: Settings | None = None,
    ) -> None:
        self._client = client
        self._settings = settings or get_settings()

    @asynccontextmanager
    async def hold(self, idempotency_key: str) -> AsyncIterator[None]:
        """在完整外部写操作期间持有一把有自动过期时间的锁。"""

        lock_name = self._lock_name(idempotency_key)
        owner_token = uuid4().hex
        client = self._client or get_redis_client()
        try:
            acquired = await client.set(
                lock_name,
                owner_token,
                nx=True,
                ex=self._settings.write_lock_ttl_seconds,
            )
        except (RedisError, OSError) as exc:
            raise WriteSafetyControlUnavailableError from exc
        if not acquired:
            raise ToolExecutionInProgressError

        try:
            yield
        finally:
            try:
                await client.eval(
                    _RELEASE_SCRIPT,
                    1,
                    lock_name,
                    owner_token,
                )
            except (RedisError, OSError):
                # 锁有 TTL，会自动释放；释放失败不能把已经成功的业务结果改成失败。
                logger.exception(
                    "Redis 写操作锁释放失败",
                    extra={"lock_name": lock_name},
                )

    def _lock_name(self, idempotency_key: str) -> str:
        digest = sha256(idempotency_key.encode("utf-8")).hexdigest()
        return f"{self._settings.redis_key_prefix}:lock:write:{digest}"
