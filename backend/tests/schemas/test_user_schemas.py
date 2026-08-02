"""用户和登录 Schema 测试。"""

import pytest
from app.schemas.auth import LoginRequest
from app.schemas.user import UserCreate, UserResponse
from pydantic import ValidationError


def test_user_create_normalizes_safe_fields_and_masks_password() -> None:
    user = UserCreate(
        email="  Graduate@Example.COM ",
        password="safe-demo-password",
        full_name="  应届生用户  ",
        timezone="Asia/Shanghai",
    )

    assert str(user.email) == "graduate@example.com"
    assert user.full_name == "应届生用户"
    assert user.password.get_secret_value() == "safe-demo-password"
    assert "safe-demo-password" not in repr(user)


def test_user_create_rejects_invalid_timezone() -> None:
    with pytest.raises(ValidationError, match="无效时区"):
        UserCreate(
            email="user@example.com",
            password="safe-demo-password",
            full_name="测试用户",
            timezone="Mars/Office",
        )


def test_login_request_normalizes_email() -> None:
    login = LoginRequest(email=" USER@Example.com ", password="secret")

    assert str(login.email) == "user@example.com"
    assert login.password.get_secret_value() == "secret"


def test_user_response_never_declares_password_fields() -> None:
    assert "password" not in UserResponse.model_fields
    assert "password_hash" not in UserResponse.model_fields
