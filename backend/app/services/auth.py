"""用户身份认证业务。"""

from collections.abc import Callable

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.audit import AuditLog
from app.models.user import User
from app.repositories.audit import AuditRepository
from app.repositories.user import UserRepository
from app.schemas.auth import LoginRequest
from app.security.password import dummy_password_hash, verify_password
from app.services.exceptions import InactiveUserError, InvalidCredentialsError


class AuthService:
    """验证邮箱、密码和用户启用状态。"""

    def __init__(
        self,
        repository: UserRepository,
        password_verifier: Callable[[str, str], bool] = verify_password,
        session: AsyncSession | None = None,
        audit_repository: AuditRepository | None = None,
    ) -> None:
        self._repository = repository
        self._password_verifier = password_verifier
        self._session = session
        self._audits = (
            audit_repository
            if audit_repository is not None
            else AuditRepository(session)
            if session is not None
            else None
        )

    async def authenticate(
        self,
        data: LoginRequest,
        *,
        request_id: str | None = None,
    ) -> User:
        """认证成功时返回用户，失败时返回稳定业务异常。"""

        user = await self._repository.get_by_email(str(data.email))
        password = data.password.get_secret_value()
        # 即使邮箱不存在也执行同等级密码校验，避免攻击者根据响应耗时枚举账号。
        password_hash = user.password_hash if user is not None else dummy_password_hash()
        password_matches = self._password_verifier(password, password_hash)
        if user is None or not password_matches:
            raise InvalidCredentialsError
        if not user.is_active:
            raise InactiveUserError
        if self._session is not None and self._audits is not None:
            try:
                await self._audits.add(
                    AuditLog(
                        user_id=user.id,
                        actor_user_id=user.id,
                        action="auth.login_succeeded",
                        resource_type="user",
                        resource_id=user.id,
                        request_id=request_id,
                        details={},
                    )
                )
                await self._session.commit()
            except Exception:
                await self._session.rollback()
                raise
        return user
