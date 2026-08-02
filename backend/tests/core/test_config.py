"""配置加载和校验测试。"""

import pytest
from app.core.config import Settings
from pydantic import ValidationError


def test_settings_normalizes_log_level() -> None:
    settings = Settings(
        _env_file=None,
        database_url="postgresql+psycopg://user:password@localhost:5432/mailpilot",
        redis_url="redis://localhost:6379/0",
        log_level="warning",
    )

    assert settings.log_level == "WARNING"


def test_settings_rejects_non_postgresql_database_url() -> None:
    with pytest.raises(ValidationError, match="postgresql\\+psycopg"):
        Settings(
            _env_file=None,
            database_url="sqlite+aiosqlite:///mailpilot.db",
            redis_url="redis://localhost:6379/0",
        )


def test_settings_rejects_invalid_redis_url() -> None:
    with pytest.raises(ValidationError, match="REDIS_URL"):
        Settings(
            _env_file=None,
            database_url="postgresql+psycopg://user:password@localhost:5432/mailpilot",
            redis_url="http://localhost:6379/0",
        )


def test_settings_rejects_short_jwt_secret() -> None:
    with pytest.raises(ValidationError, match="JWT_SECRET_KEY"):
        Settings(
            _env_file=None,
            database_url="postgresql+psycopg://user:password@localhost:5432/mailpilot",
            redis_url="redis://localhost:6379/0",
            jwt_secret_key="too-short",
        )


def test_settings_masks_demo_user_password() -> None:
    settings = Settings(
        _env_file=None,
        database_url="postgresql+psycopg://user:password@localhost:5432/mailpilot",
        redis_url="redis://localhost:6379/0",
        demo_user_password="local-demo-password",
    )

    assert settings.demo_user_password is not None
    assert settings.demo_user_password.get_secret_value() == "local-demo-password"
    assert "local-demo-password" not in repr(settings)


def test_settings_masks_valid_mcp_internal_token() -> None:
    token = "mcp-internal-token-at-least-32-characters"
    settings = Settings(
        _env_file=None,
        database_url="postgresql+psycopg://user:password@localhost:5432/mailpilot",
        redis_url="redis://localhost:6379/0",
        mcp_internal_token=token,
    )

    assert settings.mcp_internal_token is not None
    assert settings.mcp_internal_token.get_secret_value() == token
    assert token not in repr(settings)


def test_settings_rejects_short_mcp_internal_token() -> None:
    with pytest.raises(ValidationError, match="MCP_INTERNAL_TOKEN"):
        Settings(
            _env_file=None,
            database_url="postgresql+psycopg://user:password@localhost:5432/mailpilot",
            redis_url="redis://localhost:6379/0",
            mcp_internal_token="too-short",
        )


def test_settings_normalizes_optional_llm_configuration_and_masks_key() -> None:
    api_key = "local-test-llm-api-key"
    settings = Settings(
        _env_file=None,
        database_url="postgresql+psycopg://user:password@localhost:5432/mailpilot",
        redis_url="redis://localhost:6379/0",
        llm_api_key=api_key,
        llm_base_url="https://llm.example.com/v1/",
        llm_model_name="  compatible-chat-model  ",
    )

    assert settings.llm_api_key is not None
    assert settings.llm_api_key.get_secret_value() == api_key
    assert api_key not in repr(settings)
    assert settings.llm_base_url == "https://llm.example.com/v1"
    assert settings.llm_model_name == "compatible-chat-model"


def test_settings_treats_blank_llm_values_as_unconfigured() -> None:
    settings = Settings(
        _env_file=None,
        database_url="postgresql+psycopg://user:password@localhost:5432/mailpilot",
        redis_url="redis://localhost:6379/0",
        llm_api_key="",
        llm_base_url="",
        llm_model_name="",
    )

    assert settings.llm_api_key is None
    assert settings.llm_base_url is None
    assert settings.llm_model_name is None


def test_settings_rejects_invalid_llm_url() -> None:
    with pytest.raises(ValidationError, match="LLM_BASE_URL"):
        Settings(
            _env_file=None,
            database_url="postgresql+psycopg://user:password@localhost:5432/mailpilot",
            redis_url="redis://localhost:6379/0",
            llm_base_url="ftp://llm.example.com/v1",
        )


def test_settings_validation_error_never_echoes_llm_secret() -> None:
    secret = "secret-that-must-never-appear-in-validation-errors"

    with pytest.raises(ValidationError) as exc_info:
        Settings(
            _env_file=None,
            llm_api_key=secret,
            redis_url="invalid",
        )

    assert secret not in str(exc_info.value)
