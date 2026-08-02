"""用户登录接口。"""

from typing import Annotated

from fastapi import APIRouter, Depends, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.logging import request_id_context
from app.db.session import get_db_session
from app.repositories.user import UserRepository
from app.schemas.auth import LoginRequest, TokenResponse
from app.schemas.common import ApiResponse
from app.security.jwt import create_access_token
from app.security.rate_limit import LoginRateLimiter, get_login_rate_limiter
from app.services.auth import AuthService

router = APIRouter(prefix="/auth", tags=["认证"])
settings = get_settings()


@router.post(
    "/login",
    response_model=ApiResponse[TokenResponse],
    summary="邮箱密码登录",
    responses={status.HTTP_401_UNAUTHORIZED: {"description": "邮箱或密码错误"}},
)
async def login(
    data: LoginRequest,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_db_session)],
    rate_limiter: Annotated[LoginRateLimiter, Depends(get_login_rate_limiter)],
) -> ApiResponse[TokenResponse]:
    """验证用户身份并签发短期 JWT 访问令牌。"""

    await rate_limiter.check(
        email=str(data.email),
        client_host=request.client.host if request.client is not None else "unknown",
    )
    user = await AuthService(
        UserRepository(session),
        session=session,
    ).authenticate(data, request_id=request_id_context.get())
    access_token = create_access_token(user.id)
    return ApiResponse(
        success=True,
        data=TokenResponse(
            access_token=access_token,
            expires_in=settings.jwt_access_token_expire_minutes * 60,
        ),
        request_id=request_id_context.get(),
    )
