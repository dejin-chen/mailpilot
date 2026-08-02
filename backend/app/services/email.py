"""邮件导入与查询业务。"""

from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.email import EmailDirection, EmailMessage, EmailThread, EmailThreadStatus
from app.providers.mail import LocalMailProvider, MailProvider
from app.repositories.email import EmailRepository
from app.repositories.user import UserRepository
from app.schemas.email import EmailDraftCreate, EmailMessageImport
from app.services.exceptions import (
    EmailAlreadyImportedError,
    EmailDraftConflictError,
    EmailDraftNotFoundError,
    EmailImportConflictError,
    EmailSendConflictError,
    EmailThreadNotFoundError,
)


@dataclass(frozen=True, slots=True)
class ImportedEmail:
    """一次导入成功后，供上层继续使用的结构化结果。"""

    thread: EmailThread
    message: EmailMessage
    created_thread: bool


@dataclass(frozen=True, slots=True)
class EmailThreadPage:
    """邮件线程分页结果。"""

    items: list[EmailThread]
    total: int
    offset: int
    limit: int


@dataclass(frozen=True, slots=True)
class CreatedEmailDraft:
    """草稿创建结果；幂等命中时 reused 为 True。"""

    message: EmailMessage
    reused: bool


@dataclass(frozen=True, slots=True)
class SentEmail:
    """邮件发送结果；重复幂等键命中时复用已发送消息。"""

    message: EmailMessage
    reused: bool


class EmailService:
    """执行邮件业务规则，并负责事务提交和回滚。"""

    def __init__(
        self,
        session: AsyncSession,
        provider: MailProvider | None = None,
        user_repository: UserRepository | None = None,
    ) -> None:
        self._session = session
        self._provider = (
            provider if provider is not None else LocalMailProvider(EmailRepository(session))
        )
        self._user_repository = (
            user_repository if user_repository is not None else UserRepository(session)
        )

    async def import_inbound_email(
        self,
        *,
        user_id: UUID,
        data: EmailMessageImport,
    ) -> ImportedEmail:
        """幂等地导入收件邮件；并发冲突时最多自动重试一次。"""

        for attempt in range(2):
            try:
                result = await self._import_inbound_email_once(user_id=user_id, data=data)
                await self._session.commit()
                return result
            except EmailAlreadyImportedError:
                await self._session.rollback()
                raise
            except IntegrityError as exc:
                # 两个请求可能同时发现“线程不存在”。第一次冲突后回滚并重新查询，
                # 通常就能复用另一个请求刚创建的线程。
                await self._session.rollback()
                if await self._message_exists(user_id=user_id, data=data):
                    raise EmailAlreadyImportedError from exc
                if attempt == 1:
                    raise EmailImportConflictError from exc
            except Exception:
                await self._session.rollback()
                raise

        # 循环的两个分支都会 return 或 raise，此处只用于帮助类型检查器理解流程。
        raise EmailImportConflictError

    async def _import_inbound_email_once(
        self,
        *,
        user_id: UUID,
        data: EmailMessageImport,
    ) -> ImportedEmail:
        """在当前事务中组装线程和消息，不自行提交事务。"""

        if await self._message_exists(user_id=user_id, data=data):
            raise EmailAlreadyImportedError

        thread = await self._provider.get_thread_by_external_id(
            user_id=user_id,
            provider=data.provider,
            external_id=data.thread_external_id,
        )
        participants = self._collect_participants(data)
        created_thread = thread is None

        if thread is None:
            thread = EmailThread(
                user_id=user_id,
                provider=data.provider,
                external_id=data.thread_external_id,
                subject=data.subject,
                participants=participants,
                status=EmailThreadStatus.PENDING,
                last_message_at=data.sent_at,
            )
            await self._provider.add_thread(thread)
        else:
            thread.participants = sorted(set(thread.participants) | set(participants))
            thread.last_message_at = max(thread.last_message_at, data.sent_at)
            thread.status = EmailThreadStatus.PENDING
            if not thread.subject and data.subject:
                thread.subject = data.subject

        message = EmailMessage(
            user_id=user_id,
            thread_id=thread.id,
            provider=data.provider,
            external_id=data.message_external_id,
            subject=data.subject,
            sender=str(data.sender),
            recipients=[str(address) for address in data.recipients],
            cc=[str(address) for address in data.cc],
            body_text=data.body_text,
            direction=EmailDirection.INBOUND,
            headers=data.headers,
            sent_at=data.sent_at,
        )
        await self._provider.add_message(message)
        return ImportedEmail(thread=thread, message=message, created_thread=created_thread)

    async def _message_exists(
        self,
        *,
        user_id: UUID,
        data: EmailMessageImport,
    ) -> bool:
        """检查当前用户是否已经导入同一封外部邮件。"""

        message = await self._provider.get_message_by_external_id(
            user_id=user_id,
            provider=data.provider,
            external_id=data.message_external_id,
        )
        return message is not None

    async def get_thread(self, *, user_id: UUID, thread_id: UUID) -> EmailThread:
        """读取当前用户的线程详情，找不到时返回统一业务异常。"""

        thread = await self._provider.get_thread_by_id(
            user_id=user_id,
            thread_id=thread_id,
            include_messages=True,
        )
        if thread is None:
            raise EmailThreadNotFoundError
        return thread

    async def list_threads(
        self,
        *,
        user_id: UUID,
        offset: int = 0,
        limit: int = 20,
    ) -> list[EmailThread]:
        """分页读取当前用户自己的邮件线程。"""

        return await self._provider.list_threads(
            user_id=user_id,
            offset=offset,
            limit=limit,
        )

    async def list_thread_page(
        self,
        *,
        user_id: UUID,
        offset: int = 0,
        limit: int = 20,
    ) -> EmailThreadPage:
        """依次查询当前页和总数，避免同一 AsyncSession 并发执行。"""

        items = await self._provider.list_threads(
            user_id=user_id,
            offset=offset,
            limit=limit,
        )
        total = await self._provider.count_threads(user_id=user_id)
        return EmailThreadPage(items=items, total=total, offset=offset, limit=limit)

    async def search_thread_page(
        self,
        *,
        user_id: UUID,
        query: str | None = None,
        sender: str | None = None,
        status: EmailThreadStatus | None = None,
        offset: int = 0,
        limit: int = 20,
    ) -> EmailThreadPage:
        """按当前用户范围搜索邮件线程并返回分页结果。"""

        normalized_query = query.strip() if query else None
        normalized_sender = sender.strip().lower() if sender else None
        items = await self._provider.search_threads(
            user_id=user_id,
            query=normalized_query,
            sender=normalized_sender,
            status=status,
            offset=offset,
            limit=limit,
        )
        total = await self._provider.count_search_threads(
            user_id=user_id,
            query=normalized_query,
            sender=normalized_sender,
            status=status,
        )
        return EmailThreadPage(items=items, total=total, offset=offset, limit=limit)

    async def create_draft(
        self,
        *,
        user_id: UUID,
        data: EmailDraftCreate,
    ) -> CreatedEmailDraft:
        """在现有线程中幂等创建本地草稿，不发送任何外部邮件。"""

        existing = await self._provider.get_message_by_idempotency_key(
            user_id=user_id,
            idempotency_key=data.idempotency_key,
        )
        if existing is not None:
            if existing.direction is EmailDirection.DRAFT:
                return CreatedEmailDraft(message=existing, reused=True)
            raise EmailDraftConflictError

        try:
            thread = await self._provider.get_thread_by_id(
                user_id=user_id,
                thread_id=data.thread_id,
            )
            if thread is None:
                raise EmailThreadNotFoundError

            user = await self._user_repository.get_by_id(user_id)
            if user is None:
                raise EmailThreadNotFoundError

            message = EmailMessage(
                user_id=user_id,
                thread_id=thread.id,
                provider=thread.provider,
                external_id=f"draft-{uuid4()}",
                subject=data.subject if data.subject is not None else thread.subject,
                sender=user.email,
                recipients=[str(address) for address in data.recipients],
                cc=[str(address) for address in data.cc],
                body_text=data.body_text,
                direction=EmailDirection.DRAFT,
                headers={},
                sent_at=datetime.now(UTC),
                idempotency_key=data.idempotency_key,
            )
            await self._provider.add_message(message)
            await self._session.commit()
            return CreatedEmailDraft(message=message, reused=False)
        except (EmailDraftConflictError, EmailThreadNotFoundError):
            await self._session.rollback()
            raise
        except IntegrityError as exc:
            await self._session.rollback()
            existing = await self._provider.get_message_by_idempotency_key(
                user_id=user_id,
                idempotency_key=data.idempotency_key,
            )
            if existing is not None and existing.direction is EmailDirection.DRAFT:
                return CreatedEmailDraft(message=existing, reused=True)
            raise EmailDraftConflictError from exc
        except Exception:
            await self._session.rollback()
            raise

    async def mark_thread_processed(self, *, user_id: UUID, thread_id: UUID) -> EmailThread:
        """幂等地把当前用户邮件线程标记为已处理。"""

        try:
            thread = await self._provider.get_thread_by_id(
                user_id=user_id,
                thread_id=thread_id,
            )
            if thread is None:
                raise EmailThreadNotFoundError
            thread.status = EmailThreadStatus.PROCESSED
            await self._session.commit()
            return thread
        except EmailThreadNotFoundError:
            await self._session.rollback()
            raise
        except Exception:
            await self._session.rollback()
            raise

    async def send_draft(
        self,
        *,
        user_id: UUID,
        draft_message_id: UUID,
        idempotency_key: str,
    ) -> SentEmail:
        """把本地草稿复制为已发送消息，并通过幂等键避免重复发送。"""

        existing = await self._provider.get_message_by_idempotency_key(
            user_id=user_id,
            idempotency_key=idempotency_key,
        )
        if existing is not None:
            if existing.direction is EmailDirection.OUTBOUND:
                return SentEmail(message=existing, reused=True)
            raise EmailSendConflictError

        try:
            draft = await self._provider.get_message_by_id(
                user_id=user_id,
                message_id=draft_message_id,
            )
            if draft is None or draft.direction is not EmailDirection.DRAFT:
                raise EmailDraftNotFoundError

            sent = EmailMessage(
                user_id=user_id,
                thread_id=draft.thread_id,
                provider=draft.provider,
                external_id=f"sent-{uuid4()}",
                subject=draft.subject,
                sender=draft.sender,
                recipients=list(draft.recipients),
                cc=list(draft.cc),
                body_text=draft.body_text,
                direction=EmailDirection.OUTBOUND,
                headers={"X-MailPilot-Source-Draft-ID": str(draft.id)},
                sent_at=datetime.now(UTC),
                idempotency_key=idempotency_key,
            )
            await self._provider.add_message(sent)
            thread = await self._provider.get_thread_by_id(
                user_id=user_id,
                thread_id=draft.thread_id,
            )
            if thread is None:
                raise EmailThreadNotFoundError
            thread.status = EmailThreadStatus.PROCESSED
            thread.last_message_at = sent.sent_at
            await self._session.commit()
            await self._session.refresh(sent)
            return SentEmail(message=sent, reused=False)
        except (
            EmailDraftNotFoundError,
            EmailSendConflictError,
            EmailThreadNotFoundError,
        ):
            await self._session.rollback()
            raise
        except IntegrityError as exc:
            await self._session.rollback()
            existing = await self._provider.get_message_by_idempotency_key(
                user_id=user_id,
                idempotency_key=idempotency_key,
            )
            if existing is not None and existing.direction is EmailDirection.OUTBOUND:
                return SentEmail(message=existing, reused=True)
            raise EmailSendConflictError from exc
        except Exception:
            await self._session.rollback()
            raise

    @staticmethod
    def _collect_participants(data: EmailMessageImport) -> list[str]:
        """合并发件人、收件人和抄送人，并稳定去重。"""

        addresses = {
            str(data.sender),
            *(str(address) for address in data.recipients),
            *(str(address) for address in data.cc),
        }
        return sorted(addresses)
