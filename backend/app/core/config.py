"""应用配置。

所有环境差异都通过环境变量注入。模块只声明变量的类型、默认行为和校验规则，
不保存密码、令牌、模型地址或其他敏感信息。
"""

from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """MailPilot 运行配置。"""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
        hide_input_in_errors=True,
    )

    app_name: str = "MailPilot"
    app_version: str = "0.1.0"
    environment: Literal["development", "test", "production"] = "development"
    debug: bool = False
    api_v1_prefix: str = "/api/v1"

    log_level: str = "INFO"
    log_json: bool = True

    database_url: str
    database_pool_size: int = 5
    database_max_overflow: int = 10
    database_pool_recycle_seconds: int = 1800
    agent_store_timeout_seconds: float = Field(default=10.0, gt=0, le=60)

    redis_url: str
    redis_socket_timeout_seconds: float = 2.0
    redis_key_prefix: str = Field(default="mailpilot", min_length=1, max_length=50)
    login_rate_limit_enabled: bool = True
    login_rate_limit_window_seconds: int = Field(default=60, ge=10, le=3600)
    login_rate_limit_ip_attempts: int = Field(default=20, ge=1, le=1000)
    login_rate_limit_account_attempts: int = Field(default=5, ge=1, le=100)
    write_lock_ttl_seconds: int = Field(default=30, ge=5, le=300)

    jwt_secret_key: str
    jwt_algorithm: Literal["HS256", "HS384", "HS512"] = "HS256"
    jwt_access_token_expire_minutes: int = Field(default=30, ge=1, le=1440)
    jwt_issuer: str = Field(default="mailpilot", min_length=1, max_length=100)
    jwt_audience: str = Field(default="mailpilot-api", min_length=1, max_length=100)

    demo_user_email: str = "demo@example.com"
    demo_user_password: SecretStr | None = None

    llm_api_key: SecretStr | None = None
    llm_base_url: str | None = None
    llm_model_name: str | None = None
    llm_timeout_seconds: float = Field(default=30.0, gt=0, le=180)
    llm_max_retries: int = Field(default=2, ge=0, le=3)
    llm_structured_output_method: Literal["function_calling", "json_schema"] = "function_calling"

    langfuse_enabled: bool = False
    langfuse_base_url: str | None = None
    langfuse_public_key: str | None = None
    langfuse_secret_key: SecretStr | None = None
    langfuse_capture_content: bool = False

    mcp_internal_token: SecretStr | None = None
    mail_mcp_url: str = "http://localhost:8001/mcp"
    calendar_mcp_url: str = "http://localhost:8002/mcp"
    mcp_timeout_seconds: float = Field(default=10.0, gt=0, le=120)
    mcp_max_retries: int = Field(default=1, ge=0, le=3)

    cors_origins: list[str] = ["http://localhost:8501"]

    @field_validator("log_level")
    @classmethod
    def normalize_log_level(cls, value: str) -> str:
        """统一日志级别格式并拒绝无效值。"""

        normalized = value.upper()
        allowed = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
        if normalized not in allowed:
            msg = f"不支持的日志级别：{value}"
            raise ValueError(msg)
        return normalized

    @field_validator("database_url")
    @classmethod
    def validate_database_url(cls, value: str) -> str:
        """第一版只允许 SQLAlchemy 的 PostgreSQL psycopg 异步连接串。"""

        if not value.startswith("postgresql+psycopg://"):
            msg = "DATABASE_URL 必须使用 postgresql+psycopg://"
            raise ValueError(msg)
        return value

    @field_validator("redis_url")
    @classmethod
    def validate_redis_url(cls, value: str) -> str:
        """校验 Redis 连接串协议。"""

        if not value.startswith(("redis://", "rediss://")):
            msg = "REDIS_URL 必须使用 redis:// 或 rediss://"
            raise ValueError(msg)
        return value

    @field_validator("jwt_secret_key")
    @classmethod
    def validate_jwt_secret_key(cls, value: str) -> str:
        """JWT 对称签名密钥至少使用 32 个字符。"""

        if len(value) < 32:
            msg = "JWT_SECRET_KEY 长度不能少于 32 个字符"
            raise ValueError(msg)
        return value

    @field_validator("mcp_internal_token", mode="before")
    @classmethod
    def validate_mcp_internal_token(cls, value: object) -> object:
        """允许后端暂不配置 MCP；配置时内部令牌至少 32 个字符。"""

        if value in (None, ""):
            return None
        if isinstance(value, str) and len(value) < 32:
            msg = "MCP_INTERNAL_TOKEN 长度不能少于 32 个字符"
            raise ValueError(msg)
        return value

    @field_validator("llm_api_key", mode="before")
    @classmethod
    def normalize_llm_api_key(cls, value: object) -> object:
        """模型密钥允许暂不配置；配置后使用 SecretStr 避免意外输出。"""

        return None if value in (None, "") else value

    @field_validator("llm_base_url", mode="before")
    @classmethod
    def validate_llm_base_url(cls, value: object) -> object:
        """允许官方 OpenAI 使用默认地址，自定义兼容端点必须是 HTTP(S)。"""

        if value in (None, ""):
            return None
        if not isinstance(value, str) or not value.startswith(("http://", "https://")):
            msg = "LLM_BASE_URL 必须使用 http:// 或 https://"
            raise ValueError(msg)
        return value.rstrip("/")

    @field_validator("llm_model_name", mode="before")
    @classmethod
    def normalize_llm_model_name(cls, value: object) -> object:
        """空模型名按未配置处理，实际调用时再返回明确配置错误。"""

        if value in (None, ""):
            return None
        if isinstance(value, str):
            normalized = value.strip()
            return normalized or None
        return value

    @field_validator("langfuse_base_url", mode="before")
    @classmethod
    def validate_langfuse_base_url(cls, value: object) -> object:
        """Langfuse 未启用时允许留空，配置后必须使用 HTTP(S)。"""

        if value in (None, ""):
            return None
        if not isinstance(value, str) or not value.startswith(("http://", "https://")):
            msg = "LANGFUSE_BASE_URL 必须使用 http:// 或 https://"
            raise ValueError(msg)
        return value.rstrip("/")

    @field_validator("langfuse_public_key", "langfuse_secret_key", mode="before")
    @classmethod
    def normalize_optional_langfuse_credentials(cls, value: object) -> object:
        """空 Langfuse 凭证按未配置处理，工厂会安全退化为空实现。"""

        if value in (None, ""):
            return None
        if isinstance(value, str):
            normalized = value.strip()
            return normalized or None
        return value

    @field_validator("mail_mcp_url", "calendar_mcp_url")
    @classmethod
    def validate_mcp_url(cls, value: str) -> str:
        """第一版 MCP Client 只连接 HTTP(S) Streamable HTTP 地址。"""

        if not value.startswith(("http://", "https://")):
            msg = "MCP URL 必须使用 http:// 或 https://"
            raise ValueError(msg)
        return value


@lru_cache
def get_settings() -> Settings:
    """返回进程内复用的只读配置实例。"""

    return Settings()  # type: ignore[call-arg]
