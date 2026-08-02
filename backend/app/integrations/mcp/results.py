"""LangChain MCP Adapter 返回值的统一解析。"""

import json
from typing import Any

from pydantic import BaseModel, JsonValue, TypeAdapter

_JSON_VALUE_ADAPTER = TypeAdapter(JsonValue)


def normalize_mcp_output(value: Any) -> JsonValue:
    """把 Pydantic、文本和 LangChain 内容块转换为可持久化 JSON。"""

    if isinstance(value, BaseModel):
        value = value.model_dump(mode="json")
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            return value
    if isinstance(value, list) and len(value) == 1:
        block = value[0]
        if (
            isinstance(block, dict)
            and block.get("type") == "text"
            and isinstance(block.get("text"), str)
        ):
            return normalize_mcp_output(block["text"])
    return _JSON_VALUE_ADAPTER.validate_python(value)


def extract_mcp_business_error(output: JsonValue) -> tuple[str, str] | None:
    """识别 FastMCP ToolError 经 Adapter 返回的结构化错误内容。"""

    if not isinstance(output, dict) or output.get("success") is not False:
        return None
    error = output.get("error")
    if not isinstance(error, dict):
        return "MCP_TOOL_ERROR", "MCP 工具返回失败结果"
    raw_code = error.get("code")
    raw_message = error.get("message")
    code = raw_code if isinstance(raw_code, str) and len(raw_code) <= 100 else "MCP_TOOL_ERROR"
    message = (
        raw_message[:1000]
        if isinstance(raw_message, str) and raw_message
        else "MCP 工具返回失败结果"
    )
    return code, message
