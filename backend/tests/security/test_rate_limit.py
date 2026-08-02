"""Redis 登录限流单元测试。"""

from unittest.mock import AsyncMock

import pytest
from app.core.config import Settings
from app.security.exceptions import (
    LoginRateLimitExceededError,
    SecurityControlUnavailableError,
)
from app.security.rate_limit import LoginRateLimiter
from redis.exceptions import ConnectionError as RedisConnectionError


def _settings(**changes: object) -> Settings:
    values: dict[str, object] = {
        "_env_file": None,
        "database_url": "postgresql+psycopg://user:password@localhost/mailpilot",
        "redis_url": "redis://localhost/0",
        "jwt_secret_key": "test-jwt-secret-key-at-least-32-characters",
        "login_rate_limit_enabled": True,
        "login_rate_limit_ip_attempts": 10,
        "login_rate_limit_account_attempts": 3,
        "login_rate_limit_window_seconds": 60,
    }
    values.update(changes)
    return Settings(**values)  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_login_limiter_hashes_identity_and_allows_request() -> None:
    client = AsyncMock()
    client.eval.return_value = [1, 60, 1, 1]
    limiter = LoginRateLimiter(client=client, settings=_settings())

    await limiter.check(
        email="Graduate@Example.com",
        client_host="192.0.2.10",
    )

    call = client.eval.await_args
    serialized = " ".join(str(item) for item in call.args)
    assert "graduate@example.com" not in serialized.lower()
    assert "192.0.2.10" not in serialized
    assert "rate-limit:login:ip:" in serialized
    assert "rate-limit:login:account:" in serialized


@pytest.mark.asyncio
async def test_login_limiter_returns_retry_after_when_blocked() -> None:
    client = AsyncMock()
    client.eval.return_value = [0, 37, 4, 4]
    limiter = LoginRateLimiter(client=client, settings=_settings())

    with pytest.raises(LoginRateLimitExceededError) as exc_info:
        await limiter.check(email="user@example.com", client_host="127.0.0.1")

    assert exc_info.value.status_code == 429
    assert exc_info.value.headers == {"Retry-After": "37"}
    assert exc_info.value.details == {"retry_after_seconds": 37}


@pytest.mark.asyncio
async def test_login_limiter_fails_closed_when_redis_is_unavailable() -> None:
    client = AsyncMock()
    client.eval.side_effect = RedisConnectionError("redis unavailable")
    limiter = LoginRateLimiter(client=client, settings=_settings())

    with pytest.raises(SecurityControlUnavailableError):
        await limiter.check(email="user@example.com", client_host="127.0.0.1")


@pytest.mark.asyncio
async def test_disabled_login_limiter_does_not_touch_redis() -> None:
    client = AsyncMock()
    limiter = LoginRateLimiter(
        client=client,
        settings=_settings(login_rate_limit_enabled=False),
    )

    await limiter.check(email="user@example.com", client_host="127.0.0.1")

    client.eval.assert_not_awaited()
