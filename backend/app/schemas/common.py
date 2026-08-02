"""统一 API 响应结构。"""

from typing import Annotated, Any

from pydantic import BaseModel, Field

ProviderName = Annotated[
    str,
    Field(
        min_length=1,
        max_length=32,
        pattern=r"^[a-z][a-z0-9_-]*$",
        description="外部平台来源标识，例如 local、gmail 或 outlook",
    ),
]


class ErrorBody(BaseModel):
    """统一错误信息。"""

    code: str
    message: str
    details: Any | None = None


class ApiResponse[DataT](BaseModel):
    """所有业务 API 使用的响应信封。"""

    success: bool
    data: DataT | None = None
    error: ErrorBody | None = None
    request_id: str | None = None


class Page[ItemT](BaseModel):
    """统一分页响应数据。"""

    items: list[ItemT]
    total: int
    offset: int
    limit: int
