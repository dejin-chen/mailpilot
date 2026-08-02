"""用户创建业务。"""

from collections.abc import Callable

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.user import User, UserRole
from app.repositories.user import UserRepository
from app.schemas.user import UserCreate
from app.security.password import hash_password
from app.services.exceptions import UserAlreadyExistsError


class UserService:
    """组织用户创建规则并控制数据库事务。"""

    def __init__(
        self,
        session: AsyncSession,
        repository: UserRepository | None = None,
        password_hasher: Callable[[str], str] = hash_password,
    ) -> None:
        self._session = session
        self._repository = repository if repository is not None else UserRepository(session)
        self._password_hasher = password_hasher

    async def create_user(self, data: UserCreate) -> User:
        """创建普通用户；成功提交，任何失败都回滚。"""

        try:
            email = str(data.email)
            if await self._repository.get_by_email(email) is not None:
                raise UserAlreadyExistsError

            user = User(
                email=email,
                password_hash=self._password_hasher(data.password.get_secret_value()),
                full_name=data.full_name,
                role=UserRole.USER,
                is_active=True,
                timezone=data.timezone,
            )
            await self._repository.add(user)
            await self._session.commit()
            return user
        except UserAlreadyExistsError:
            await self._session.rollback()
            raise
        except IntegrityError as exc:
            await self._session.rollback()
            raise UserAlreadyExistsError from exc
        except Exception:
            await self._session.rollback()
            raise
