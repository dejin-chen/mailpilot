"""当前用户接口。"""

from typing import Annotated

from fastapi import APIRouter, Depends, status

from app.core.logging import request_id_context
from app.models.user import User
from app.schemas.common import ApiResponse
from app.schemas.user import UserResponse
from app.security.dependencies import get_current_user

router = APIRouter(prefix="/users", tags=["用户"])


@router.get(
    "/me",
    response_model=ApiResponse[UserResponse],
    summary="查询当前用户",
    responses={status.HTTP_401_UNAUTHORIZED: {"description": "访问令牌无效或缺失"}},
)
async def get_me(
    current_user: Annotated[User, Depends(get_current_user)],
) -> ApiResponse[UserResponse]:
    """返回当前登录用户的安全公开信息。"""

    return ApiResponse(
        success=True,
        data=UserResponse.model_validate(current_user),
        request_id=request_id_context.get(),
    )
