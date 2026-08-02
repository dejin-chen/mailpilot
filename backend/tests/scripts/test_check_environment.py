"""生产环境预检测试。"""

from backend.scripts.check_environment import validate_environment


def _valid_values() -> dict[str, str]:
    return {
        "ENVIRONMENT": "production",
        "DEBUG": "false",
        "POSTGRES_PASSWORD": "strong-postgres-password",
        "DOCKER_DATABASE_URL": (
            "postgresql+psycopg://mailpilot:strong-postgres-password@postgres:5432/mailpilot"
        ),
        "REDIS_PASSWORD": "strong-redis-password",
        "DOCKER_REDIS_URL": "redis://:strong-redis-password@redis:6379/0",
        "JWT_SECRET_KEY": "j" * 40,
        "MCP_INTERNAL_TOKEN": "m" * 40,
        "LLM_API_KEY": "secret-model-key",
        "LLM_BASE_URL": "https://model.example.com/v1",
        "LLM_MODEL_NAME": "compatible-chat-model",
        "CORS_ORIGINS": "[]",
        "LANGFUSE_ENABLED": "false",
        "LANGFUSE_CAPTURE_CONTENT": "false",
    }


def test_valid_production_environment_passes_without_exposing_values() -> None:
    errors, warnings = validate_environment(_valid_values())

    assert errors == []
    assert warnings == []


def test_placeholders_wildcard_cors_and_internal_hosts_are_rejected() -> None:
    values = _valid_values()
    values.update(
        {
            "JWT_SECRET_KEY": "CHANGE_ME_RANDOM_STRING_AT_LEAST_32_CHARACTERS",
            "DOCKER_DATABASE_URL": (
                "postgresql+psycopg://mailpilot:password@localhost:5432/mailpilot"
            ),
            "CORS_ORIGINS": '["*"]',
            "LLM_BASE_URL": "https://your-provider.example.com/v1",
        }
    )

    errors, _ = validate_environment(values)

    assert "JWT_SECRET_KEY 仍包含示例占位符" in errors
    assert "DOCKER_DATABASE_URL 在 Compose 中应连接主机 postgres" in errors
    assert "生产环境 CORS_ORIGINS 不允许使用通配符 *" in errors
    assert "LLM_BASE_URL 仍包含示例占位符" in errors
