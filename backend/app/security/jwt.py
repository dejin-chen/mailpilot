"""JWT 访问令牌签发与验证。"""

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import jwt
from jwt.exceptions import InvalidTokenError
from pydantic import ValidationError

from app.core.config import get_settings
from app.schemas.auth import TokenPayload


class AccessTokenError(ValueError):
    """访问令牌无效、过期或结构不完整。"""


def create_access_token(subject: UUID, *, expires_delta: timedelta | None = None) -> str:
    """为用户签发带过期时间的访问令牌。"""

    settings = get_settings()
    issued_at = datetime.now(UTC)
    lifetime = expires_delta or timedelta(minutes=settings.jwt_access_token_expire_minutes)
    payload = {
        "sub": str(subject),
        "token_type": "access",
        "iss": settings.jwt_issuer,
        "aud": settings.jwt_audience,
        "jti": str(uuid4()),
        "iat": issued_at,
        "exp": issued_at + lifetime,
    }
    return jwt.encode(payload, settings.jwt_secret_key, algorithm=settings.jwt_algorithm)


def decode_access_token(token: str) -> TokenPayload:
    """验证签名、算法、过期时间和载荷结构。"""

    settings = get_settings()
    try:
        payload = jwt.decode(
            token,
            settings.jwt_secret_key,
            algorithms=[settings.jwt_algorithm],
            issuer=settings.jwt_issuer,
            audience=settings.jwt_audience,
            options={
                "require": [
                    "sub",
                    "token_type",
                    "iss",
                    "aud",
                    "jti",
                    "iat",
                    "exp",
                ]
            },
        )
        return TokenPayload.model_validate(payload)
    except (InvalidTokenError, ValidationError) as exc:
        raise AccessTokenError("访问令牌无效或已过期") from exc
