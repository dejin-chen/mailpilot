"""邮件 ORM 模型测试。"""

from app.models import EmailDirection, EmailMessage, EmailThread, EmailThreadStatus


def test_email_thread_table_contract() -> None:
    assert set(EmailThread.__table__.columns.keys()) == {
        "id",
        "user_id",
        "provider",
        "external_id",
        "subject",
        "participants",
        "status",
        "last_message_at",
        "created_at",
        "updated_at",
    }
    assert {constraint.name for constraint in EmailThread.__table__.constraints} == {
        "pk_email_threads",
        "fk_email_threads_user_id_users",
        "uq_email_threads_id_user",
        "uq_email_threads_user_provider_external",
        "ck_email_threads_email_thread_status",
    }
    assert {index.name for index in EmailThread.__table__.indexes} == {
        "ix_email_threads_user_status",
        "ix_email_threads_user_updated_at",
    }


def test_email_message_table_contract_and_user_safe_foreign_key() -> None:
    assert {constraint.name for constraint in EmailMessage.__table__.constraints} == {
        "pk_email_messages",
        "fk_email_messages_user_id_users",
        "fk_email_messages_thread_user_email_threads",
        "uq_email_messages_user_provider_external",
        "uq_email_messages_user_idempotency_key",
        "ck_email_messages_email_direction",
    }
    composite_foreign_key = next(
        constraint
        for constraint in EmailMessage.__table__.foreign_key_constraints
        if constraint.name == "fk_email_messages_thread_user_email_threads"
    )
    assert [column.name for column in composite_foreign_key.columns] == ["thread_id", "user_id"]


def test_email_enum_values_match_database_contract() -> None:
    assert [status.value for status in EmailThreadStatus] == [
        "pending",
        "processing",
        "processed",
        "failed",
    ]
    assert [direction.value for direction in EmailDirection] == [
        "inbound",
        "outbound",
        "draft",
    ]


def test_messages_relationship_forbids_implicit_lazy_loading() -> None:
    assert EmailThread.messages.property.lazy == "raise"
    assert EmailMessage.thread.property.lazy == "raise"
