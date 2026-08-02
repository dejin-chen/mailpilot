"""用户请求与响应 Schema。"""

from datetime import datetime
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, EmailStr, Field, SecretStr, field_validator

from app.models.user import UserRole


class UserCreate(BaseModel):
    """创建普通用户时允许接收的数据。"""

    email: EmailStr
    password: SecretStr = Field(min_length=8, max_length=128)
    full_name: str = Field(min_length=1, max_length=100)
    timezone: str = Field(default="UTC", min_length=1, max_length=64)

    @field_validator("email", mode="before")
    @classmethod
    def normalize_email(cls, value: object) -> object:
        """邮箱去除首尾空格并统一为小写。"""

        return value.strip().lower() if isinstance(value, str) else value

    @field_validator("full_name")
    @classmethod
    def normalize_full_name(cls, value: str) -> str:
        """姓名去除首尾空格，并拒绝纯空白内容。"""

        normalized = value.strip()
        if not normalized:
            msg = "姓名不能为空"
            raise ValueError(msg)
        return normalized

    @field_validator("timezone")
    @classmethod
    def validate_timezone(cls, value: str) -> str:
        """只接受 Python 时区数据库能够识别的时区。"""

        try:
            ZoneInfo(value)
        except ZoneInfoNotFoundError as exc:
            msg = f"无效时区：{value}"
            raise ValueError(msg) from exc
        return value


class UserResponse(BaseModel):
    """允许通过 API 返回的用户公开信息。"""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    email: EmailStr
    full_name: str
    role: UserRole
    is_active: bool
    timezone: str
    created_at: datetime
    updated_at: datetime
