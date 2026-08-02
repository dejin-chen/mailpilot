"""真实 Redis 限流与分布式锁集成测试。"""

import os
from uuid import uuid4

import pytest
from app.core.config import Settings
from app.reliability.locks import RedisWriteLockManager
from app.security.exceptions import LoginRateLimitExceededError
from app.security.rate_limit import LoginRateLimiter
from app.services.exceptions import ToolExecutionInProgressError
from redis.asyncio import Redis

pytestmark = pytest.mark.integration


def _settings(*, prefix: str) -> Settings:
    return Settings(
        _env_file=None,
        database_url="postgresql+psycopg://user:password@localhost/mailpilot",
        redis_url="redis://localhost/0",
        jwt_secret_key="test-jwt-secret-key-at-least-32-characters",
        redis_key_prefix=prefix,
        login_rate_limit_enabled=True,
        login_rate_limit_ip_attempts=2,
        login_rate_limit_account_attempts=2,
        login_rate_limit_window_seconds=30,
        write_lock_ttl_seconds=10,
    )


async def test_real_redis_enforces_login_limit_and_write_lock() -> None:
    redis_url = os.getenv("TEST_REDIS_URL")
    if not redis_url:
        pytest.skip("未配置 TEST_REDIS_URL，跳过真实 Redis 安全控制测试")

    prefix = f"mailpilot-test-{uuid4().hex}"
    client = Redis.from_url(redis_url, encoding="utf-8", decode_responses=True)
    settings = _settings(prefix=prefix)
    limiter = LoginRateLimiter(client=client, settings=settings)
    first_lock = RedisWriteLockManager(client=client, settings=settings)
    second_lock = RedisWriteLockManager(client=client, settings=settings)
    try:
        await limiter.check(email="rate@example.com", client_host="192.0.2.1")
        await limiter.check(email="rate@example.com", client_host="192.0.2.1")
        with pytest.raises(LoginRateLimitExceededError):
            await limiter.check(email="rate@example.com", client_host="192.0.2.1")

        async with first_lock.hold("write-operation-001"):
            with pytest.raises(ToolExecutionInProgressError):
                async with second_lock.hold("write-operation-001"):
                    pytest.fail("同一幂等操作不能同时获得两把锁")
    finally:
        keys = [key async for key in client.scan_iter(match=f"{prefix}:*")]
        if keys:
            await client.delete(*keys)
        await client.aclose()
