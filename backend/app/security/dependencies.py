"""FastAPI 当前用户和权限依赖。"""

from typing import Annotated

from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db_session
from app.models.user import User, UserRole
from app.repositories.user import UserRepository
from app.security.exceptions import (
    AdministratorRequiredError,
    AuthenticationRequiredError,
    InvalidAccessTokenError,
)
from app.security.jwt import AccessTokenError, decode_access_token
from app.services.exceptions import InactiveUserError

bearer_scheme = HTTPBearer(
    auto_error=False,
    bearerFormat="JWT",
    description="请输入登录接口返回的 JWT 访问令牌",
)


async def get_current_user(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> User:
    """验证 JWT，并从数据库加载当前启用用户。"""

    if credentials is None:
        raise AuthenticationRequiredError

    try:
        payload = decode_access_token(credentials.credentials)
    except AccessTokenError as exc:
        raise InvalidAccessTokenError from exc

    user = await UserRepository(session).get_by_id(payload.sub)
    if user is None:
        raise InvalidAccessTokenError
    if not user.is_active:
        raise InactiveUserError
    return user


async def get_current_admin(
    current_user: Annotated[User, Depends(get_current_user)],
) -> User:
    """要求当前用户已登录且角色为管理员。"""

    if current_user.role is not UserRole.ADMIN:
        raise AdministratorRequiredError
    return current_user
