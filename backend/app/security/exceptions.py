"""Bearer 认证异常。"""

from app.core.exceptions import AppException

_BEARER_HEADERS = {"WWW-Authenticate": "Bearer"}


class AuthenticationRequiredError(AppException):
    """请求没有提供 Bearer Token。"""

    def __init__(self) -> None:
        super().__init__(
            code="AUTHENTICATION_REQUIRED",
            message="需要提供访问令牌",
            status_code=401,
            headers=_BEARER_HEADERS,
        )


class InvalidAccessTokenError(AppException):
    """访问令牌无法通过安全校验。"""

    def __init__(self) -> None:
        super().__init__(
            code="INVALID_ACCESS_TOKEN",
            message="访问令牌无效或已过期",
            status_code=401,
            headers=_BEARER_HEADERS,
        )


class AdministratorRequiredError(AppException):
    """当前用户已经登录，但不具备管理员角色。"""

    def __init__(self) -> None:
        super().__init__(
            code="ADMINISTRATOR_REQUIRED",
            message="当前操作需要管理员权限",
            status_code=403,
        )


class LoginRateLimitExceededError(AppException):
    """登录尝试超过当前时间窗口允许次数。"""

    def __init__(self, retry_after_seconds: int) -> None:
        retry_after = max(1, retry_after_seconds)
        super().__init__(
            code="LOGIN_RATE_LIMIT_EXCEEDED",
            message="登录尝试过于频繁，请稍后重试",
            status_code=429,
            details={"retry_after_seconds": retry_after},
            headers={"Retry-After": str(retry_after)},
        )


class SecurityControlUnavailableError(AppException):
    """登录安全控制依赖的 Redis 暂时不可用。"""

    def __init__(self) -> None:
        super().__init__(
            code="SECURITY_CONTROL_UNAVAILABLE",
            message="登录安全服务暂时不可用，请稍后重试",
            status_code=503,
        )
