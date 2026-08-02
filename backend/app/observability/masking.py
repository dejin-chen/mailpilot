"""发送到外部观察平台前的递归敏感信息脱敏。"""

import re
from collections.abc import Mapping, Sequence
from datetime import date, datetime
from enum import Enum
from typing import Any
from uuid import UUID

from pydantic import BaseModel, SecretStr

_EMAIL_PATTERN = re.compile(r"(?<![\w.+-])[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
_BEARER_PATTERN = re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]+")
_SECRET_KEYS = {
    "authorization",
    "api_key",
    "apikey",
    "password",
    "secret",
    "secret_key",
    "token",
    "access_token",
    "refresh_token",
}
_MAX_TEXT_LENGTH = 4000


def mask_sensitive_data(value: object) -> object:
    """递归脱敏邮箱、令牌、SecretStr 和常见敏感字段。"""

    if isinstance(value, SecretStr):
        return "[已脱敏]"
    if isinstance(value, BaseModel):
        return mask_sensitive_data(value.model_dump(mode="json"))
    if isinstance(value, Mapping):
        return {
            str(key): (
                "[已脱敏]" if str(key).lower() in _SECRET_KEYS else mask_sensitive_data(item)
            )
            for key, item in value.items()
        }
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [mask_sensitive_data(item) for item in value]
    if isinstance(value, str):
        masked = _EMAIL_PATTERN.sub("[邮箱已脱敏]", value)
        masked = _BEARER_PATTERN.sub("Bearer [已脱敏]", masked)
        if len(masked) > _MAX_TEXT_LENGTH:
            return f"{masked[:_MAX_TEXT_LENGTH]}…[内容已截断]"
        return masked
    if isinstance(value, (UUID, datetime, date, Enum)):
        return str(value.value if isinstance(value, Enum) else value)
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return str(value)


def summarize_value(value: object) -> dict[str, Any]:
    """默认只记录结构和字段名，避免把完整邮件或 Prompt 发到外部。"""

    if isinstance(value, BaseModel):
        return {
            "type": type(value).__name__,
            "fields": sorted(value.model_fields_set),
        }
    if isinstance(value, Mapping):
        return {
            "type": "mapping",
            "keys": sorted(str(key) for key in value),
        }
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return {"type": "sequence", "length": len(value)}
    return {"type": type(value).__name__}
