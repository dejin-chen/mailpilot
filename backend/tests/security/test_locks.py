"""Redis 写操作锁单元测试。"""

from unittest.mock import AsyncMock

import pytest
from app.core.config import Settings
from app.reliability.locks import RedisWriteLockManager
from app.services.exceptions import (
    ToolExecutionInProgressError,
    WriteSafetyControlUnavailableError,
)
from redis.exceptions import ConnectionError as RedisConnectionError


def _settings() -> Settings:
    return Settings(
        _env_file=None,
        database_url="postgresql+psycopg://user:password@localhost/mailpilot",
        redis_url="redis://localhost/0",
        jwt_secret_key="test-jwt-secret-key-at-least-32-characters",
        redis_key_prefix="mailpilot-test",
        write_lock_ttl_seconds=15,
    )


@pytest.mark.asyncio
async def test_write_lock_uses_unique_owner_and_safe_release() -> None:
    client = AsyncMock()
    client.set.return_value = True
    client.eval.return_value = 1
    manager = RedisWriteLockManager(client=client, settings=_settings())

    async with manager.hold("write:approval-1:send_email:v1"):
        pass

    set_call = client.set.await_args
    lock_name, owner_token = set_call.args
    assert lock_name.startswith("mailpilot-test:lock:write:")
    assert "approval-1" not in lock_name
    assert len(owner_token) == 32
    assert set_call.kwargs == {"nx": True, "ex": 15}
    release_call = client.eval.await_args
    assert release_call.args[-2:] == (lock_name, owner_token)


@pytest.mark.asyncio
async def test_write_lock_rejects_concurrent_holder() -> None:
    client = AsyncMock()
    client.set.return_value = None
    manager = RedisWriteLockManager(client=client, settings=_settings())

    with pytest.raises(ToolExecutionInProgressError):
        async with manager.hold("same-operation"):
            pytest.fail("未获得锁时不应进入业务代码")

    client.eval.assert_not_awaited()


@pytest.mark.asyncio
async def test_write_lock_fails_closed_when_redis_is_unavailable() -> None:
    client = AsyncMock()
    client.set.side_effect = RedisConnectionError("redis unavailable")
    manager = RedisWriteLockManager(client=client, settings=_settings())

    with pytest.raises(WriteSafetyControlUnavailableError):
        async with manager.hold("same-operation"):
            pytest.fail("Redis 失效时不应执行写操作")
