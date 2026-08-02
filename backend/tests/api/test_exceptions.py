"""统一异常响应测试。"""

from typing import Annotated

import pytest
from app.core.exceptions import AppException, register_exception_handlers
from app.core.logging import RequestContextMiddleware
from fastapi import FastAPI, Path
from httpx import ASGITransport, AsyncClient

exception_app = FastAPI()
exception_app.add_middleware(RequestContextMiddleware)
register_exception_handlers(exception_app)


@exception_app.get("/business-error")
async def business_error() -> None:
    """触发一个可安全返回的业务异常。"""

    raise AppException(code="DEMO_CONFLICT", message="演示资源冲突", status_code=409)


@exception_app.get("/items/{item_id}")
async def get_item(item_id: Annotated[int, Path(gt=0)]) -> dict[str, int]:
    """用于触发路径参数校验异常。"""

    return {"item_id": item_id}


@pytest.mark.asyncio
async def test_app_exception_uses_unified_error_envelope() -> None:
    transport = ASGITransport(app=exception_app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(
            "/business-error", headers={"X-Request-ID": "business-error-001"}
        )

    assert response.status_code == 409
    payload = response.json()
    assert payload["success"] is False
    assert payload["error"]["code"] == "DEMO_CONFLICT"
    assert payload["error"]["message"] == "演示资源冲突"
    assert payload["request_id"] == "business-error-001"


@pytest.mark.asyncio
async def test_validation_exception_hides_raw_internal_structure() -> None:
    transport = ASGITransport(app=exception_app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/items/not-an-integer")

    assert response.status_code == 422
    payload = response.json()
    assert payload["success"] is False
    assert payload["error"]["code"] == "VALIDATION_ERROR"
    assert payload["error"]["message"] == "请求参数校验失败"
    assert payload["error"]["details"][0]["location"] == ["path", "item_id"]


@pytest.mark.asyncio
async def test_framework_404_uses_same_safe_error_envelope() -> None:
    transport = ASGITransport(app=exception_app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(
            "/path-does-not-exist",
            headers={"X-Request-ID": "safe-http-error-001"},
        )

    assert response.status_code == 404
    assert response.json() == {
        "success": False,
        "data": None,
        "error": {
            "code": "HTTP_ERROR",
            "message": "请求的接口不存在",
            "details": None,
        },
        "request_id": "safe-http-error-001",
    }
