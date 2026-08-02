"""在启动生产 Compose 前检查环境文件，不输出任何秘密值。"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from urllib.parse import urlparse

from dotenv import dotenv_values

PLACEHOLDER_MARKERS = ("CHANGE_ME", "replace_with", "your-")
REQUIRED_KEYS = (
    "POSTGRES_PASSWORD",
    "DOCKER_DATABASE_URL",
    "REDIS_PASSWORD",
    "DOCKER_REDIS_URL",
    "JWT_SECRET_KEY",
    "MCP_INTERNAL_TOKEN",
    "LLM_API_KEY",
    "LLM_MODEL_NAME",
)


def validate_environment(values: dict[str, str | None]) -> tuple[list[str], list[str]]:
    """返回错误和警告；消息只包含变量名，不回显变量值。"""

    errors: list[str] = []
    warnings: list[str] = []

    if values.get("ENVIRONMENT") != "production":
        errors.append("ENVIRONMENT 必须是 production")
    if str(values.get("DEBUG", "")).lower() != "false":
        errors.append("DEBUG 必须是 false")

    for key in REQUIRED_KEYS:
        value = (values.get(key) or "").strip()
        if not value:
            errors.append(f"{key} 不能为空")
        elif any(marker.lower() in value.lower() for marker in PLACEHOLDER_MARKERS):
            errors.append(f"{key} 仍包含示例占位符")

    for key in ("JWT_SECRET_KEY", "MCP_INTERNAL_TOKEN"):
        value = values.get(key) or ""
        if value and len(value) < 32:
            errors.append(f"{key} 长度不能少于 32 个字符")

    _validate_url(
        values,
        key="DOCKER_DATABASE_URL",
        schemes={"postgresql+psycopg"},
        expected_host="postgres",
        errors=errors,
    )
    _validate_url(
        values,
        key="DOCKER_REDIS_URL",
        schemes={"redis", "rediss"},
        expected_host="redis",
        errors=errors,
    )
    llm_base_url = (values.get("LLM_BASE_URL") or "").strip()
    if llm_base_url:
        if any(marker.lower() in llm_base_url.lower() for marker in PLACEHOLDER_MARKERS):
            errors.append("LLM_BASE_URL 仍包含示例占位符")
        elif urlparse(llm_base_url).scheme not in {"http", "https"}:
            errors.append("LLM_BASE_URL 必须使用 http 或 https")

    try:
        cors_origins = json.loads(values.get("CORS_ORIGINS") or "[]")
    except json.JSONDecodeError:
        errors.append("CORS_ORIGINS 必须是 JSON 数组")
    else:
        if not isinstance(cors_origins, list):
            errors.append("CORS_ORIGINS 必须是 JSON 数组")
        elif "*" in cors_origins:
            errors.append("生产环境 CORS_ORIGINS 不允许使用通配符 *")
        elif any("localhost" in str(origin) for origin in cors_origins):
            warnings.append("CORS_ORIGINS 仍包含 localhost，请确认这符合部署方式")

    langfuse_enabled = str(values.get("LANGFUSE_ENABLED", "false")).lower() == "true"
    if langfuse_enabled:
        for key in ("LANGFUSE_BASE_URL", "LANGFUSE_PUBLIC_KEY", "LANGFUSE_SECRET_KEY"):
            value = (values.get(key) or "").strip()
            if not value:
                errors.append(f"启用 Langfuse 时 {key} 不能为空")
            elif any(marker.lower() in value.lower() for marker in PLACEHOLDER_MARKERS):
                errors.append(f"{key} 仍包含示例占位符")

    if str(values.get("LANGFUSE_CAPTURE_CONTENT", "false")).lower() == "true":
        warnings.append("LANGFUSE_CAPTURE_CONTENT=true 会增加邮件内容外发风险")

    return errors, warnings


def _validate_url(
    values: dict[str, str | None],
    *,
    key: str,
    schemes: set[str],
    expected_host: str,
    errors: list[str],
) -> None:
    value = (values.get(key) or "").strip()
    if not value:
        return
    parsed = urlparse(value)
    if parsed.scheme not in schemes:
        errors.append(f"{key} 使用了不支持的协议")
    if parsed.hostname != expected_host:
        errors.append(f"{key} 在 Compose 中应连接主机 {expected_host}")


def main() -> None:
    parser = argparse.ArgumentParser(description="检查 MailPilot 生产环境文件")
    parser.add_argument("--env-file", default=".env.production")
    args = parser.parse_args()

    env_path = Path(args.env_file)
    if not env_path.is_file():
        raise SystemExit(f"环境文件不存在：{env_path}")

    values = dict(dotenv_values(env_path))
    errors, warnings = validate_environment(values)
    for warning in warnings:
        print(f"[警告] {warning}")
    if errors:
        for error in errors:
            print(f"[错误] {error}")
        raise SystemExit(1)
    print("生产环境检查通过，未输出任何秘密值。")


if __name__ == "__main__":
    main()
