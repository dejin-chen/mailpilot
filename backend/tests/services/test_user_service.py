"""用户创建业务单元测试。"""

from unittest.mock import AsyncMock, Mock

import pytest
from app.models.user import UserRole
from app.repositories.user import UserRepository
from app.schemas.user import UserCreate
from app.services.exceptions import UserAlreadyExistsError
from app.services.user import UserService
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession


def _user_create() -> UserCreate:
    return UserCreate(
        email="graduate@example.com",
        password="safe-demo-password",
        full_name="应届生用户",
        timezone="Asia/Shanghai",
    )


@pytest.mark.asyncio
async def test_create_user_hashes_password_and_commits() -> None:
    session = AsyncMock(spec=AsyncSession)
    repository = AsyncMock(spec=UserRepository)
    repository.get_by_email.return_value = None
    password_hasher = Mock(return_value="argon2-test-hash")
    service = UserService(session, repository, password_hasher)

    user = await service.create_user(_user_create())

    assert user.email == "graduate@example.com"
    assert user.password_hash == "argon2-test-hash"
    assert user.role is UserRole.USER
    password_hasher.assert_called_once_with("safe-demo-password")
    repository.add.assert_awaited_once_with(user)
    session.commit.assert_awaited_once()
    session.rollback.assert_not_awaited()


@pytest.mark.asyncio
async def test_create_user_rolls_back_when_email_exists() -> None:
    session = AsyncMock(spec=AsyncSession)
    repository = AsyncMock(spec=UserRepository)
    repository.get_by_email.return_value = object()
    service = UserService(session, repository, Mock(return_value="unused"))

    with pytest.raises(UserAlreadyExistsError) as exc_info:
        await service.create_user(_user_create())

    assert exc_info.value.code == "USER_ALREADY_EXISTS"
    repository.add.assert_not_awaited()
    session.commit.assert_not_awaited()
    session.rollback.assert_awaited_once()


@pytest.mark.asyncio
async def test_create_user_maps_unique_constraint_race_and_rolls_back() -> None:
    session = AsyncMock(spec=AsyncSession)
    repository = AsyncMock(spec=UserRepository)
    repository.get_by_email.return_value = None
    repository.add.side_effect = IntegrityError("INSERT", {}, Exception("unique conflict"))
    service = UserService(session, repository, Mock(return_value="argon2-test-hash"))

    with pytest.raises(UserAlreadyExistsError):
        await service.create_user(_user_create())

    session.commit.assert_not_awaited()
    session.rollback.assert_awaited_once()
