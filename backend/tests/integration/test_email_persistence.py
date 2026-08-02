"""邮件 Repository、Service 与 PostgreSQL 集成测试。"""

from datetime import UTC, datetime, timedelta

import pytest
from app.models.email import EmailMessage, EmailThread
from app.repositories.email import EmailRepository
from app.schemas.email import EmailMessageImport, EmailThreadDetailResponse
from app.schemas.user import UserCreate
from app.services.email import EmailService
from app.services.exceptions import EmailAlreadyImportedError
from app.services.user import UserService
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = pytest.mark.integration


def _user_create(email: str) -> UserCreate:
    return UserCreate(
        email=email,
        password="integration-password",
        full_name="邮件集成测试用户",
        timezone="Asia/Shanghai",
    )


def _email_import(**changes: object) -> EmailMessageImport:
    payload: dict[str, object] = {
        "provider": "local",
        "thread_external_id": "thread-001",
        "message_external_id": "message-001",
        "subject": "项目周会安排",
        "sender": "boss@example.com",
        "recipients": ["mail-owner@example.com"],
        "cc": [],
        "body_text": "请确认明天下午是否可以开会。",
        "headers": {"Message-ID": "message-001"},
        "sent_at": datetime(2026, 7, 19, 10, 0, tzinfo=UTC),
    }
    payload.update(changes)
    return EmailMessageImport.model_validate(payload)


async def test_import_and_query_thread_with_messages(
    integration_session: AsyncSession,
) -> None:
    user = await UserService(integration_session).create_user(
        _user_create("mail-owner@example.com")
    )
    service = EmailService(integration_session)

    imported = await service.import_inbound_email(user_id=user.id, data=_email_import())
    stored = await service.get_thread(user_id=user.id, thread_id=imported.thread.id)
    response = EmailThreadDetailResponse.model_validate(stored)

    assert response.id == imported.thread.id
    assert len(response.messages) == 1
    assert response.messages[0].external_id == "message-001"
    assert response.messages[0].sent_at.tzinfo is not None


async def test_follow_up_email_reuses_thread_and_keeps_message_order(
    integration_session: AsyncSession,
) -> None:
    user = await UserService(integration_session).create_user(_user_create("follow-up@example.com"))
    service = EmailService(integration_session)
    first_time = datetime(2026, 7, 19, 10, 0, tzinfo=UTC)
    first = await service.import_inbound_email(
        user_id=user.id,
        data=_email_import(sent_at=first_time),
    )
    second = await service.import_inbound_email(
        user_id=user.id,
        data=_email_import(
            message_external_id="message-002",
            sent_at=first_time + timedelta(hours=1),
        ),
    )

    stored = await service.get_thread(user_id=user.id, thread_id=first.thread.id)

    assert second.thread.id == first.thread.id
    assert [message.external_id for message in stored.messages] == [
        "message-001",
        "message-002",
    ]


async def test_duplicate_message_rolls_back_without_extra_rows(
    integration_session: AsyncSession,
) -> None:
    user = await UserService(integration_session).create_user(_user_create("duplicate@example.com"))
    user_id = user.id
    service = EmailService(integration_session)
    await service.import_inbound_email(user_id=user_id, data=_email_import())

    with pytest.raises(EmailAlreadyImportedError):
        await service.import_inbound_email(user_id=user_id, data=_email_import())

    message_count = await integration_session.scalar(
        select(func.count()).select_from(EmailMessage).where(EmailMessage.user_id == user_id)
    )
    assert message_count == 1


async def test_same_external_ids_are_isolated_between_users(
    integration_session: AsyncSession,
) -> None:
    first_user = await UserService(integration_session).create_user(
        _user_create("first-owner@example.com")
    )
    second_user = await UserService(integration_session).create_user(
        _user_create("second-owner@example.com")
    )
    service = EmailService(integration_session)

    first = await service.import_inbound_email(user_id=first_user.id, data=_email_import())
    second = await service.import_inbound_email(user_id=second_user.id, data=_email_import())

    assert first.thread.id != second.thread.id
    first_threads = await EmailRepository(integration_session).list_threads(user_id=first_user.id)
    second_threads = await EmailRepository(integration_session).list_threads(user_id=second_user.id)
    total_threads = await integration_session.scalar(
        select(func.count())
        .select_from(EmailThread)
        .where(EmailThread.user_id.in_([first_user.id, second_user.id]))
    )
    assert [thread.id for thread in first_threads] == [first.thread.id]
    assert [thread.id for thread in second_threads] == [second.thread.id]
    assert total_threads == 2
