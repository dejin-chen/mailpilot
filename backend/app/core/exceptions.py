"""应用异常和 FastAPI 全局异常处理。"""

import logging
from collections.abc import Mapping
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.core.logging import request_id_context
from app.schemas.common import ApiResponse, ErrorBody

logger = logging.getLogger(__name__)


class AppException(Exception):
    """可安全返回给调用方的业务异常。"""

    def __init__(
        self,
        *,
        code: str,
        message: str,
        status_code: int = 400,
        details: Any | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code
        self.details = details
        self.headers = dict(headers) if headers is not None else None


def _error_response(
    *,
    code: str,
    message: str,
    status_code: int,
    details: Any | None = None,
    headers: Mapping[str, str] | None = None,
) -> JSONResponse:
    payload = ApiResponse[object](
        success=False,
        error=ErrorBody(code=code, message=message, details=details),
        request_id=request_id_context.get(),
    )
    return JSONResponse(
        status_code=status_code,
        content=payload.model_dump(mode="json"),
        headers=headers,
    )


def register_exception_handlers(app: FastAPI) -> None:
    """注册统一异常响应，避免泄露内部堆栈和敏感信息。"""

    @app.exception_handler(AppException)
    async def handle_app_exception(_: Request, exc: AppException) -> JSONResponse:
        return _error_response(
            code=exc.code,
            message=exc.message,
            status_code=exc.status_code,
            details=exc.details,
            headers=exc.headers,
        )

    @app.exception_handler(RequestValidationError)
    async def handle_validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
        details = [
            {"location": list(error["loc"]), "message": error["msg"], "type": error["type"]}
            for error in exc.errors()
        ]
        return _error_response(
            code="VALIDATION_ERROR",
            message="请求参数校验失败",
            status_code=422,
            details=details,
        )

    @app.exception_handler(StarletteHTTPException)
    async def handle_http_error(_: Request, exc: StarletteHTTPException) -> JSONResponse:
        messages = {
            404: "请求的接口不存在",
            405: "当前接口不支持该请求方法",
        }
        return _error_response(
            code="HTTP_ERROR",
            message=messages.get(exc.status_code, "请求处理失败"),
            status_code=exc.status_code,
            headers=exc.headers,
        )

    @app.exception_handler(Exception)
    async def handle_unexpected_error(_: Request, exc: Exception) -> JSONResponse:
        logger.exception("未处理的应用异常", exc_info=exc)
        return _error_response(
            code="INTERNAL_SERVER_ERROR",
            message="服务暂时不可用，请稍后重试",
            status_code=500,
        )
