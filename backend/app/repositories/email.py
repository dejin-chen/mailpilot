"""邮件线程与消息的数据访问。"""

from uuid import UUID

from sqlalchemy import ColumnElement, Select, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.email import EmailMessage, EmailThread, EmailThreadStatus


class EmailRepository:
    """封装邮件表查询和写入，不负责提交或回滚事务。"""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_thread_by_external_id(
        self,
        *,
        user_id: UUID,
        provider: str,
        external_id: str,
    ) -> EmailThread | None:
        """在指定用户范围内按 Provider 线程 ID 查询。"""

        statement = select(EmailThread).where(
            EmailThread.user_id == user_id,
            EmailThread.provider == provider,
            EmailThread.external_id == external_id,
        )
        return await self._session.scalar(statement)

    async def get_message_by_external_id(
        self,
        *,
        user_id: UUID,
        provider: str,
        external_id: str,
    ) -> EmailMessage | None:
        """在指定用户范围内查询外部邮件是否已经导入。"""

        statement = select(EmailMessage).where(
            EmailMessage.user_id == user_id,
            EmailMessage.provider == provider,
            EmailMessage.external_id == external_id,
        )
        return await self._session.scalar(statement)

    async def get_message_by_idempotency_key(
        self,
        *,
        user_id: UUID,
        idempotency_key: str,
    ) -> EmailMessage | None:
        """按当前用户的幂等键查询邮件消息。"""

        statement = select(EmailMessage).where(
            EmailMessage.user_id == user_id,
            EmailMessage.idempotency_key == idempotency_key,
        )
        return await self._session.scalar(statement)

    async def get_message_by_id(
        self,
        *,
        user_id: UUID,
        message_id: UUID,
    ) -> EmailMessage | None:
        """按主键读取当前用户的一封邮件消息。"""

        statement = select(EmailMessage).where(
            EmailMessage.id == message_id,
            EmailMessage.user_id == user_id,
        )
        return await self._session.scalar(statement)

    async def get_thread_by_id(
        self,
        *,
        user_id: UUID,
        thread_id: UUID,
        include_messages: bool = False,
    ) -> EmailThread | None:
        """按主键查询当前用户的线程，可显式预加载其中的消息。"""

        statement: Select[tuple[EmailThread]] = select(EmailThread).where(
            EmailThread.id == thread_id,
            EmailThread.user_id == user_id,
        )
        if include_messages:
            statement = statement.options(selectinload(EmailThread.messages))
        return await self._session.scalar(statement)

    async def list_threads(
        self,
        *,
        user_id: UUID,
        offset: int = 0,
        limit: int = 20,
    ) -> list[EmailThread]:
        """分页查询当前用户的邮件线程，最新邮件排在最前面。"""

        statement = (
            select(EmailThread)
            .where(EmailThread.user_id == user_id)
            .order_by(EmailThread.last_message_at.desc(), EmailThread.id.desc())
            .offset(offset)
            .limit(limit)
        )
        result = await self._session.scalars(statement)
        return list(result.all())

    async def count_threads(self, *, user_id: UUID) -> int:
        """统计当前用户的邮件线程总数。"""

        statement = (
            select(func.count()).select_from(EmailThread).where(EmailThread.user_id == user_id)
        )
        return int(await self._session.scalar(statement) or 0)

    async def search_threads(
        self,
        *,
        user_id: UUID,
        query: str | None = None,
        sender: str | None = None,
        status: EmailThreadStatus | None = None,
        offset: int = 0,
        limit: int = 20,
    ) -> list[EmailThread]:
        """按主题、正文、发件人和状态搜索当前用户的邮件线程。"""

        conditions = self._search_conditions(
            user_id=user_id,
            query=query,
            sender=sender,
            status=status,
        )
        statement = (
            select(EmailThread)
            .where(*conditions)
            .order_by(EmailThread.last_message_at.desc(), EmailThread.id.desc())
            .offset(offset)
            .limit(limit)
        )
        result = await self._session.scalars(statement)
        return list(result.all())

    async def count_search_threads(
        self,
        *,
        user_id: UUID,
        query: str | None = None,
        sender: str | None = None,
        status: EmailThreadStatus | None = None,
    ) -> int:
        """统计满足搜索条件的当前用户邮件线程。"""

        statement = (
            select(func.count())
            .select_from(EmailThread)
            .where(
                *self._search_conditions(
                    user_id=user_id,
                    query=query,
                    sender=sender,
                    status=status,
                )
            )
        )
        return int(await self._session.scalar(statement) or 0)

    async def add_thread(self, thread: EmailThread) -> EmailThread:
        """把新线程加入 Session 并 flush，以便获得数据库生成的数据。"""

        self._session.add(thread)
        await self._session.flush()
        return thread

    async def add_message(self, message: EmailMessage) -> EmailMessage:
        """把新消息加入 Session 并 flush，事务提交由 Service 决定。"""

        self._session.add(message)
        await self._session.flush()
        return message

    @staticmethod
    def _search_conditions(
        *,
        user_id: UUID,
        query: str | None,
        sender: str | None,
        status: EmailThreadStatus | None,
    ) -> list[ColumnElement[bool]]:
        """生成参数化搜索条件，用户输入不会拼接成 SQL。"""

        conditions: list[ColumnElement[bool]] = [EmailThread.user_id == user_id]
        if query:
            escaped = query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            pattern = f"%{escaped}%"
            conditions.append(
                or_(
                    EmailThread.subject.ilike(pattern, escape="\\"),
                    EmailThread.messages.any(EmailMessage.body_text.ilike(pattern, escape="\\")),
                )
            )
        if sender:
            conditions.append(EmailThread.messages.any(EmailMessage.sender == sender.lower()))
        if status is not None:
            conditions.append(EmailThread.status == status)
        return conditions
