"""基于 Redis 的登录尝试限流。"""

from __future__ import annotations

from hashlib import sha256
from typing import Protocol

from redis.exceptions import RedisError

from app.core.config import Settings, get_settings
from app.db.redis import get_redis_client
from app.security.exceptions import (
    LoginRateLimitExceededError,
    SecurityControlUnavailableError,
)

_LOGIN_LIMIT_SCRIPT = """
local ip_count = redis.call('INCR', KEYS[1])
if ip_count == 1 then
  redis.call('EXPIRE', KEYS[1], ARGV[3])
end
local account_count = redis.call('INCR', KEYS[2])
if account_count == 1 then
  redis.call('EXPIRE', KEYS[2], ARGV[3])
end
local ip_ttl = redis.call('TTL', KEYS[1])
local account_ttl = redis.call('TTL', KEYS[2])
local allowed = 0
if ip_count <= tonumber(ARGV[1]) and account_count <= tonumber(ARGV[2]) then
  allowed = 1
end
return {allowed, math.max(ip_ttl, account_ttl), ip_count, account_count}
""".strip()


class RedisScriptClient(Protocol):
    """限流器依赖的最小 Redis 接口，便于单元测试注入 Fake。"""

    async def eval(
        self,
        script: str,
        numkeys: int,
        *keys_and_args: object,
    ) -> object: ...


class LoginRateLimiter:
    """同时按来源地址和账号统计登录失败前的尝试次数。"""

    def __init__(
        self,
        *,
        client: RedisScriptClient | None = None,
        settings: Settings | None = None,
    ) -> None:
        self._client = client
        self._settings = settings or get_settings()

    async def check(self, *, email: str, client_host: str) -> None:
        """计入一次登录尝试，超限时返回带 Retry-After 的稳定错误。"""

        if not self._settings.login_rate_limit_enabled:
            return

        prefix = self._settings.redis_key_prefix
        ip_digest = _digest(client_host or "unknown")
        account_digest = _digest(email.strip().lower())
        client = self._client or get_redis_client()
        try:
            raw_result = await client.eval(
                _LOGIN_LIMIT_SCRIPT,
                2,
                f"{prefix}:rate-limit:login:ip:{ip_digest}",
                f"{prefix}:rate-limit:login:account:{account_digest}",
                self._settings.login_rate_limit_ip_attempts,
                self._settings.login_rate_limit_account_attempts,
                self._settings.login_rate_limit_window_seconds,
            )
        except (RedisError, OSError) as exc:
            raise SecurityControlUnavailableError from exc

        if (
            not isinstance(raw_result, (list, tuple))
            or len(raw_result) < 2
            or not all(isinstance(item, int) for item in raw_result[:2])
        ):
            raise SecurityControlUnavailableError
        allowed, retry_after = int(raw_result[0]), int(raw_result[1])
        if allowed != 1:
            raise LoginRateLimitExceededError(retry_after)


def get_login_rate_limiter() -> LoginRateLimiter:
    """FastAPI 依赖入口；测试可以通过 dependency_overrides 替换。"""

    return LoginRateLimiter()


def _digest(value: str) -> str:
    """Redis Key 不保存原始邮箱或客户端地址。"""

    return sha256(value.encode("utf-8")).hexdigest()[:32]
