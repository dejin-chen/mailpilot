"""MCP Client 稳定错误。"""

from app.core.exceptions import AppException


class McpConfigurationError(AppException):
    """MCP Client 缺少安全配置。"""

    def __init__(self) -> None:
        super().__init__(
            code="MCP_CONFIGURATION_ERROR",
            message="MCP Client 配置不完整",
            status_code=500,
        )


class McpToolNotAllowedError(AppException):
    """调用方尝试使用当前工作流未授权的工具。"""

    def __init__(self, tool_name: str) -> None:
        super().__init__(
            code="MCP_TOOL_NOT_ALLOWED",
            message=f"工具 {tool_name} 不在当前允许列表中",
            status_code=403,
        )


class McpToolUnavailableError(AppException):
    """允许列表中的工具未被 Server 正常发现。"""

    def __init__(self, tool_name: str) -> None:
        super().__init__(
            code="MCP_TOOL_UNAVAILABLE",
            message=f"工具 {tool_name} 当前不可用",
            status_code=503,
        )


class McpToolInvocationError(AppException):
    """工具连接、超时或执行失败。"""

    def __init__(self, tool_name: str) -> None:
        super().__init__(
            code="MCP_TOOL_INVOCATION_FAILED",
            message=f"工具 {tool_name} 调用失败",
            status_code=502,
        )
