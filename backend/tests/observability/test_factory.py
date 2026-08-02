"""可观测性工厂安全退化测试。"""

from app.core.config import Settings
from app.observability.factory import build_observability
from app.observability.noop import NoOpObservability


def _settings(**changes: object) -> Settings:
    values: dict[str, object] = {
        "_env_file": None,
        "database_url": "postgresql+psycopg://user:password@localhost:5432/mailpilot",
        "redis_url": "redis://localhost:6379/0",
        "jwt_secret_key": "test-jwt-secret-key-at-least-32-characters",
    }
    values.update(changes)
    return Settings(**values)  # type: ignore[arg-type]


def test_disabled_langfuse_uses_noop() -> None:
    observer = build_observability(_settings(langfuse_enabled=False))

    assert isinstance(observer, NoOpObservability)
    assert observer.enabled is False


def test_incomplete_langfuse_credentials_do_not_break_core() -> None:
    observer = build_observability(
        _settings(
            langfuse_enabled=True,
            langfuse_public_key="pk-test",
            langfuse_secret_key=None,
        )
    )

    assert isinstance(observer, NoOpObservability)


def test_settings_normalizes_langfuse_url_and_masks_secret() -> None:
    settings = _settings(
        langfuse_enabled=True,
        langfuse_base_url="https://langfuse.example.com/",
        langfuse_public_key="pk-test",
        langfuse_secret_key="sk-test-secret",
    )

    assert settings.langfuse_base_url == "https://langfuse.example.com"
    assert settings.langfuse_secret_key is not None
    assert "sk-test-secret" not in repr(settings)
