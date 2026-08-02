"""用户数据访问。"""

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.user import User


class UserRepository:
    """封装 users 表查询和写入，不控制事务提交。"""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_by_id(self, user_id: UUID) -> User | None:
        """按主键查询用户。"""

        return await self._session.scalar(select(User).where(User.id == user_id))

    async def get_by_email(self, email: str) -> User | None:
        """按已标准化的小写邮箱查询用户。"""

        return await self._session.scalar(select(User).where(User.email == email.lower()))

    async def add(self, user: User) -> User:
        """加入 Session 并 flush，提交事务由 Service 决定。"""

        self._session.add(user)
        await self._session.flush()
        return user
