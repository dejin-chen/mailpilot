"""用户认证业务单元测试。"""

from unittest.mock import AsyncMock, Mock

import pytest
from app.models.user import User
from app.repositories.user import UserRepository
from app.schemas.auth import LoginRequest
from app.services.auth import AuthService
from app.services.exceptions import InactiveUserError, InvalidCredentialsError


def _login() -> LoginRequest:
    return LoginRequest(email="graduate@example.com", password="correct-password")


def _user(*, is_active: bool = True) -> User:
    return User(
        email="graduate@example.com",
        password_hash="stored-hash",
        full_name="企业测试用户",
        is_active=is_active,
        timezone="Asia/Shanghai",
    )


@pytest.mark.asyncio
async def test_authenticate_returns_active_user_for_correct_password() -> None:
    repository = AsyncMock(spec=UserRepository)
    user = _user()
    repository.get_by_email.return_value = user
    verifier = Mock(return_value=True)
    service = AuthService(repository, verifier)

    result = await service.authenticate(_login())

    assert result is user
    verifier.assert_called_once_with("correct-password", "stored-hash")


@pytest.mark.asyncio
@pytest.mark.parametrize("user,verified", [(None, False), (_user(), False)])
async def test_authenticate_hides_missing_user_and_wrong_password(
    user: User | None, verified: bool
) -> None:
    repository = AsyncMock(spec=UserRepository)
    repository.get_by_email.return_value = user
    verifier = Mock(return_value=verified)
    service = AuthService(repository, verifier)

    with pytest.raises(InvalidCredentialsError) as exc_info:
        await service.authenticate(_login())

    assert exc_info.value.code == "INVALID_CREDENTIALS"
    verifier.assert_called_once()
    assert verifier.call_args.args[0] == "correct-password"
    if user is None:
        assert verifier.call_args.args[1] != ""
        assert verifier.call_args.args[1] != "stored-hash"


@pytest.mark.asyncio
async def test_authenticate_rejects_inactive_user() -> None:
    repository = AsyncMock(spec=UserRepository)
    repository.get_by_email.return_value = _user(is_active=False)
    service = AuthService(repository, Mock(return_value=True))

    with pytest.raises(InactiveUserError) as exc_info:
        await service.authenticate(_login())

    assert exc_info.value.code == "USER_INACTIVE"
