"""登录与 JWT Schema。"""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, EmailStr, Field, SecretStr, field_validator


class LoginRequest(BaseModel):
    """邮箱密码登录请求。"""

    email: EmailStr
    password: SecretStr = Field(min_length=1, max_length=128)

    @field_validator("email", mode="before")
    @classmethod
    def normalize_email(cls, value: object) -> object:
        """登录邮箱统一为小写。"""

        return value.strip().lower() if isinstance(value, str) else value


class TokenResponse(BaseModel):
    """登录成功后返回的访问令牌。"""

    access_token: str
    token_type: Literal["bearer"] = "bearer"
    expires_in: int = Field(gt=0)


class TokenPayload(BaseModel):
    """验证 JWT 后得到的可信载荷。"""

    sub: UUID
    token_type: Literal["access"]
    issuer: str = Field(alias="iss", min_length=1, max_length=100)
    audience: str = Field(alias="aud", min_length=1, max_length=100)
    token_id: UUID = Field(alias="jti")
    issued_at: datetime = Field(alias="iat")
    expires_at: datetime = Field(alias="exp")
