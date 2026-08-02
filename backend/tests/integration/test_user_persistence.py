"""用户 Repository、Service 和 PostgreSQL 集成测试。"""

import pytest
from app.models.user import User
from app.repositories.user import UserRepository
from app.schemas.auth import LoginRequest
from app.schemas.user import UserCreate, UserResponse
from app.services.auth import AuthService
from app.services.exceptions import InvalidCredentialsError, UserAlreadyExistsError
from app.services.user import UserService
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = pytest.mark.integration


def _user_create() -> UserCreate:
    return UserCreate(
        email="integration@example.com",
        password="integration-password",
        full_name="集成测试用户",
        timezone="Asia/Shanghai",
    )


async def test_create_and_query_user_with_real_postgresql(
    integration_session: AsyncSession,
) -> None:
    service = UserService(integration_session)

    created = await service.create_user(_user_create())
    stored = await UserRepository(integration_session).get_by_id(created.id)
    response = UserResponse.model_validate(stored)

    assert stored is not None
    assert response.email == "integration@example.com"
    assert response.timezone == "Asia/Shanghai"
    assert response.created_at.tzinfo is not None
    assert "password_hash" not in response.model_dump()


async def test_duplicate_email_rolls_back_without_extra_row(
    integration_session: AsyncSession,
) -> None:
    service = UserService(integration_session)
    await service.create_user(_user_create())

    with pytest.raises(UserAlreadyExistsError):
        await service.create_user(_user_create())

    count = await integration_session.scalar(
        select(func.count()).select_from(User).where(User.email == "integration@example.com")
    )
    assert count == 1


async def test_authenticate_reads_real_password_hash(
    integration_session: AsyncSession,
) -> None:
    created = await UserService(integration_session).create_user(_user_create())
    auth_service = AuthService(UserRepository(integration_session))

    authenticated = await auth_service.authenticate(
        LoginRequest(email=created.email, password="integration-password")
    )
    assert authenticated.id == created.id

    with pytest.raises(InvalidCredentialsError):
        await auth_service.authenticate(
            LoginRequest(email=created.email, password="wrong-password")
        )
