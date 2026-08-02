"""邮件 Provider 接口与 PostgreSQL 本地实现。"""

from typing import Protocol
from uuid import UUID

from app.models.email import EmailMessage, EmailThread, EmailThreadStatus
from app.repositories.email import EmailRepository


class MailProvider(Protocol):
    """EmailService 所依赖的邮件存取能力合同。"""

    async def get_thread_by_external_id(
        self,
        *,
        user_id: UUID,
        provider: str,
        external_id: str,
    ) -> EmailThread | None: ...

    async def get_message_by_external_id(
        self,
        *,
        user_id: UUID,
        provider: str,
        external_id: str,
    ) -> EmailMessage | None: ...

    async def get_message_by_idempotency_key(
        self,
        *,
        user_id: UUID,
        idempotency_key: str,
    ) -> EmailMessage | None: ...

    async def get_message_by_id(
        self,
        *,
        user_id: UUID,
        message_id: UUID,
    ) -> EmailMessage | None: ...

    async def get_thread_by_id(
        self,
        *,
        user_id: UUID,
        thread_id: UUID,
        include_messages: bool = False,
    ) -> EmailThread | None: ...

    async def list_threads(
        self,
        *,
        user_id: UUID,
        offset: int = 0,
        limit: int = 20,
    ) -> list[EmailThread]: ...

    async def count_threads(self, *, user_id: UUID) -> int: ...

    async def search_threads(
        self,
        *,
        user_id: UUID,
        query: str | None = None,
        sender: str | None = None,
        status: EmailThreadStatus | None = None,
        offset: int = 0,
        limit: int = 20,
    ) -> list[EmailThread]: ...

    async def count_search_threads(
        self,
        *,
        user_id: UUID,
        query: str | None = None,
        sender: str | None = None,
        status: EmailThreadStatus | None = None,
    ) -> int: ...

    async def add_thread(self, thread: EmailThread) -> EmailThread: ...

    async def add_message(self, message: EmailMessage) -> EmailMessage: ...


class LocalMailProvider:
    """通过 Repository 使用 PostgreSQL 模拟企业邮箱。"""

    def __init__(self, repository: EmailRepository) -> None:
        self._repository = repository

    async def get_thread_by_external_id(
        self,
        *,
        user_id: UUID,
        provider: str,
        external_id: str,
    ) -> EmailThread | None:
        return await self._repository.get_thread_by_external_id(
            user_id=user_id,
            provider=provider,
            external_id=external_id,
        )

    async def get_message_by_external_id(
        self,
        *,
        user_id: UUID,
        provider: str,
        external_id: str,
    ) -> EmailMessage | None:
        return await self._repository.get_message_by_external_id(
            user_id=user_id,
            provider=provider,
            external_id=external_id,
        )

    async def get_message_by_idempotency_key(
        self,
        *,
        user_id: UUID,
        idempotency_key: str,
    ) -> EmailMessage | None:
        return await self._repository.get_message_by_idempotency_key(
            user_id=user_id,
            idempotency_key=idempotency_key,
        )

    async def get_message_by_id(
        self,
        *,
        user_id: UUID,
        message_id: UUID,
    ) -> EmailMessage | None:
        return await self._repository.get_message_by_id(
            user_id=user_id,
            message_id=message_id,
        )

    async def get_thread_by_id(
        self,
        *,
        user_id: UUID,
        thread_id: UUID,
        include_messages: bool = False,
    ) -> EmailThread | None:
        return await self._repository.get_thread_by_id(
            user_id=user_id,
            thread_id=thread_id,
            include_messages=include_messages,
        )

    async def list_threads(
        self,
        *,
        user_id: UUID,
        offset: int = 0,
        limit: int = 20,
    ) -> list[EmailThread]:
        return await self._repository.list_threads(
            user_id=user_id,
            offset=offset,
            limit=limit,
        )

    async def count_threads(self, *, user_id: UUID) -> int:
        return await self._repository.count_threads(user_id=user_id)

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
        return await self._repository.search_threads(
            user_id=user_id,
            query=query,
            sender=sender,
            status=status,
            offset=offset,
            limit=limit,
        )

    async def count_search_threads(
        self,
        *,
        user_id: UUID,
        query: str | None = None,
        sender: str | None = None,
        status: EmailThreadStatus | None = None,
    ) -> int:
        return await self._repository.count_search_threads(
            user_id=user_id,
            query=query,
            sender=sender,
            status=status,
        )

    async def add_thread(self, thread: EmailThread) -> EmailThread:
        return await self._repository.add_thread(thread)

    async def add_message(self, message: EmailMessage) -> EmailMessage:
        return await self._repository.add_message(message)
