"""业务异常到 MCP Tool Error 的安全映射。"""

import json
import logging
from typing import NoReturn

from mcp.server.fastmcp.exceptions import ToolError

from app.core.exceptions import AppException

logger = logging.getLogger(__name__)


def raise_mcp_tool_error(exc: Exception, *, request_id: str) -> NoReturn:
    """保留稳定业务错误码，未知异常只返回通用错误。"""

    if isinstance(exc, AppException):
        code = exc.code
        message = exc.message
    else:
        logger.exception("MCP 工具执行失败", exc_info=exc, extra={"request_id": request_id})
        code = "MCP_INTERNAL_ERROR"
        message = "工具执行失败，请稍后重试"

    payload = {
        "success": False,
        "error": {"code": code, "message": message},
        "request_id": request_id,
    }
    raise ToolError(json.dumps(payload, ensure_ascii=False, separators=(",", ":"))) from exc
