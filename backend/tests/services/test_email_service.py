"""邮件导入业务单元测试。"""

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from app.models.email import (
    EmailDirection,
    EmailMessage,
    EmailThread,
    EmailThreadStatus,
)
from app.models.user import User
from app.repositories.email import EmailRepository
from app.repositories.user import UserRepository
from app.schemas.email import EmailDraftCreate, EmailMessageImport
from app.services.email import EmailService
from app.services.exceptions import EmailAlreadyImportedError, EmailThreadNotFoundError
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession


def _email_import(**changes: object) -> EmailMessageImport:
    payload: dict[str, object] = {
        "provider": "local",
        "thread_external_id": "thread-001",
        "message_external_id": "message-001",
        "subject": "项目周会安排",
        "sender": "boss@example.com",
        "recipients": ["user@example.com"],
        "cc": ["team@example.com"],
        "body_text": "请确认明天下午是否可以开会。",
        "headers": {},
        "sent_at": datetime(2026, 7, 19, 10, 0, tzinfo=UTC),
    }
    payload.update(changes)
    return EmailMessageImport.model_validate(payload)


@pytest.mark.asyncio
async def test_import_new_email_creates_thread_and_commits() -> None:
    session = AsyncMock(spec=AsyncSession)
    repository = AsyncMock(spec=EmailRepository)
    repository.get_message_by_external_id.return_value = None
    repository.get_thread_by_external_id.return_value = None
    generated_thread_id = uuid4()

    async def assign_thread_id(thread: EmailThread) -> EmailThread:
        thread.id = generated_thread_id
        return thread

    repository.add_thread.side_effect = assign_thread_id
    service = EmailService(session, repository)

    result = await service.import_inbound_email(user_id=uuid4(), data=_email_import())

    assert result.created_thread is True
    assert result.thread.status is EmailThreadStatus.PENDING
    assert result.thread.participants == [
        "boss@example.com",
        "team@example.com",
        "user@example.com",
    ]
    assert result.message.thread_id == generated_thread_id
    assert result.message.direction is EmailDirection.INBOUND
    repository.add_thread.assert_awaited_once_with(result.thread)
    repository.add_message.assert_awaited_once_with(result.message)
    session.commit.assert_awaited_once()
    session.rollback.assert_not_awaited()


@pytest.mark.asyncio
async def test_import_follow_up_reuses_and_updates_existing_thread() -> None:
    session = AsyncMock(spec=AsyncSession)
    repository = AsyncMock(spec=EmailRepository)
    repository.get_message_by_external_id.return_value = None
    previous_time = datetime(2026, 7, 19, 9, 0, tzinfo=UTC)
    existing = EmailThread(
        id=uuid4(),
        user_id=uuid4(),
        provider="local",
        external_id="thread-001",
        subject="项目周会安排",
        participants=["boss@example.com"],
        status=EmailThreadStatus.PROCESSED,
        last_message_at=previous_time,
    )
    repository.get_thread_by_external_id.return_value = existing
    service = EmailService(session, repository)
    new_time = previous_time + timedelta(hours=1)

    result = await service.import_inbound_email(
        user_id=existing.user_id,
        data=_email_import(message_external_id="message-002", sent_at=new_time),
    )

    assert result.created_thread is False
    assert result.thread is existing
    assert existing.last_message_at == new_time
    assert existing.status is EmailThreadStatus.PENDING
    assert "user@example.com" in existing.participants
    repository.add_thread.assert_not_awaited()
    repository.add_message.assert_awaited_once()
    session.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_import_duplicate_email_rolls_back_without_writing() -> None:
    session = AsyncMock(spec=AsyncSession)
    repository = AsyncMock(spec=EmailRepository)
    repository.get_message_by_external_id.return_value = object()
    service = EmailService(session, repository)

    with pytest.raises(EmailAlreadyImportedError):
        await service.import_inbound_email(user_id=uuid4(), data=_email_import())

    repository.add_thread.assert_not_awaited()
    repository.add_message.assert_not_awaited()
    session.commit.assert_not_awaited()
    session.rollback.assert_awaited_once()


@pytest.mark.asyncio
async def test_import_maps_concurrent_duplicate_after_integrity_error() -> None:
    session = AsyncMock(spec=AsyncSession)
    repository = AsyncMock(spec=EmailRepository)
    repository.get_message_by_external_id.side_effect = [None, object()]
    repository.get_thread_by_external_id.return_value = None
    repository.add_thread.side_effect = IntegrityError("INSERT", {}, Exception("conflict"))
    service = EmailService(session, repository)

    with pytest.raises(EmailAlreadyImportedError):
        await service.import_inbound_email(user_id=uuid4(), data=_email_import())

    session.rollback.assert_awaited_once()
    session.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_get_thread_hides_missing_or_other_users_thread() -> None:
    session = AsyncMock(spec=AsyncSession)
    repository = AsyncMock(spec=EmailRepository)
    repository.get_thread_by_id.return_value = None
    service = EmailService(session, repository)

    with pytest.raises(EmailThreadNotFoundError):
        await service.get_thread(user_id=uuid4(), thread_id=uuid4())

    repository.get_thread_by_id.assert_awaited_once()


@pytest.mark.asyncio
async def test_search_threads_keeps_normalized_filters_and_pagination() -> None:
    session = AsyncMock(spec=AsyncSession)
    provider = AsyncMock(spec=EmailRepository)
    provider.search_threads.return_value = []
    provider.count_search_threads.return_value = 0
    service = EmailService(session, provider)
    user_id = uuid4()

    page = await service.search_thread_page(
        user_id=user_id,
        query="  周会  ",
        sender=" BOSS@Example.com ",
        status=EmailThreadStatus.PENDING,
        offset=2,
        limit=5,
    )

    assert page.total == 0
    provider.search_threads.assert_awaited_once_with(
        user_id=user_id,
        query="周会",
        sender="boss@example.com",
        status=EmailThreadStatus.PENDING,
        offset=2,
        limit=5,
    )


@pytest.mark.asyncio
async def test_create_draft_uses_user_email_and_commits() -> None:
    session = AsyncMock(spec=AsyncSession)
    provider = AsyncMock(spec=EmailRepository)
    users = AsyncMock(spec=UserRepository)
    user_id = uuid4()
    thread = EmailThread(
        id=uuid4(),
        user_id=user_id,
        provider="local",
        external_id="thread-001",
        subject="原始主题",
        participants=[],
        status=EmailThreadStatus.PENDING,
        last_message_at=datetime(2026, 7, 19, 10, 0, tzinfo=UTC),
    )
    provider.get_message_by_idempotency_key.return_value = None
    provider.get_thread_by_id.return_value = thread
    users.get_by_id.return_value = User(
        id=user_id,
        email="owner@example.com",
        password_hash="hash",
        full_name="用户",
        timezone="Asia/Shanghai",
    )
    service = EmailService(session, provider, users)
    data = EmailDraftCreate(
        thread_id=thread.id,
        recipients=["boss@example.com"],
        body_text="确认参加会议。",
        idempotency_key="draft-run-001",
    )

    result = await service.create_draft(user_id=user_id, data=data)

    assert result.reused is False
    assert result.message.sender == "owner@example.com"
    assert result.message.subject == "原始主题"
    assert result.message.direction is EmailDirection.DRAFT
    assert result.message.idempotency_key == "draft-run-001"
    provider.add_message.assert_awaited_once_with(result.message)
    session.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_mark_thread_processed_is_idempotent_business_update() -> None:
    session = AsyncMock(spec=AsyncSession)
    provider = AsyncMock(spec=EmailRepository)
    thread = EmailThread(
        id=uuid4(),
        user_id=uuid4(),
        provider="local",
        external_id="thread-001",
        subject="主题",
        participants=[],
        status=EmailThreadStatus.PROCESSED,
        last_message_at=datetime(2026, 7, 19, 10, 0, tzinfo=UTC),
    )
    provider.get_thread_by_id.return_value = thread
    service = EmailService(session, provider)

    result = await service.mark_thread_processed(user_id=thread.user_id, thread_id=thread.id)

    assert result.status is EmailThreadStatus.PROCESSED
    session.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_send_draft_creates_one_outbound_message_with_idempotency_key() -> None:
    session = AsyncMock(spec=AsyncSession)
    provider = AsyncMock(spec=EmailRepository)
    user_id = uuid4()
    thread = EmailThread(
        id=uuid4(),
        user_id=user_id,
        provider="local",
        external_id="thread-send-001",
        subject="确认会议",
        participants=[],
        status=EmailThreadStatus.PENDING,
        last_message_at=datetime(2026, 7, 24, 8, 0, tzinfo=UTC),
    )
    draft = EmailMessage(
        id=uuid4(),
        user_id=user_id,
        thread_id=thread.id,
        provider="local",
        external_id="draft-send-001",
        subject="确认会议",
        sender="owner@example.com",
        recipients=["manager@example.com"],
        cc=[],
        body_text="我会准时参加。",
        direction=EmailDirection.DRAFT,
        headers={},
        sent_at=datetime(2026, 7, 24, 8, 5, tzinfo=UTC),
        idempotency_key="draft-create-001",
    )
    provider.get_message_by_idempotency_key.return_value = None
    provider.get_message_by_id.return_value = draft
    provider.get_thread_by_id.return_value = thread
    service = EmailService(session, provider)

    result = await service.send_draft(
        user_id=user_id,
        draft_message_id=draft.id,
        idempotency_key="write-send-001",
    )

    assert result.reused is False
    assert result.message.direction is EmailDirection.OUTBOUND
    assert result.message.idempotency_key == "write-send-001"
    assert result.message.body_text == draft.body_text
    assert thread.status is EmailThreadStatus.PROCESSED
    provider.add_message.assert_awaited_once_with(result.message)
    session.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_send_draft_reuses_existing_outbound_without_second_write() -> None:
    session = AsyncMock(spec=AsyncSession)
    provider = AsyncMock(spec=EmailRepository)
    existing = EmailMessage(
        id=uuid4(),
        user_id=uuid4(),
        thread_id=uuid4(),
        provider="local",
        external_id="sent-001",
        subject="已发送",
        sender="owner@example.com",
        recipients=["manager@example.com"],
        cc=[],
        body_text="已发送内容",
        direction=EmailDirection.OUTBOUND,
        headers={},
        sent_at=datetime(2026, 7, 24, 8, 10, tzinfo=UTC),
        idempotency_key="write-send-reused",
    )
    provider.get_message_by_idempotency_key.return_value = existing
    service = EmailService(session, provider)

    result = await service.send_draft(
        user_id=existing.user_id,
        draft_message_id=uuid4(),
        idempotency_key="write-send-reused",
    )

    assert result.reused is True
    assert result.message is existing
    provider.get_message_by_id.assert_not_awaited()
    provider.add_message.assert_not_awaited()
    session.commit.assert_not_awaited()
