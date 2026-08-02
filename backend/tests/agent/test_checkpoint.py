"""Checkpointer URL 与安全序列化器测试。"""

from datetime import UTC, datetime
from uuid import uuid4

from app.agent.approval_schemas import SendEmailArguments, WriteActionProposal
from app.agent.checkpoint import (
    build_checkpoint_database_url,
    build_checkpoint_serializer,
)
from app.memory.schemas import StoredMemoryEntry
from app.models.approval import ApprovalAction
from app.models.memory import MemoryType
from pydantic import BaseModel


class UntrustedCheckpointType(BaseModel):
    """不在 MailPilot Checkpointer 允许列表中的测试类型。"""

    value: str


def test_checkpoint_database_url_removes_sqlalchemy_driver() -> None:
    converted = build_checkpoint_database_url(
        "postgresql+psycopg://user:p%40ss@localhost:5432/mailpilot"
    )

    assert converted == "postgresql://user:p%40ss@localhost:5432/mailpilot"
    assert "+psycopg" not in converted


def test_checkpoint_serializer_round_trips_allowed_agent_type() -> None:
    serializer = build_checkpoint_serializer()
    proposal = WriteActionProposal(
        action=ApprovalAction.SEND_EMAIL,
        arguments=SendEmailArguments(draft_message_id=uuid4()),
        summary="发送审批邮件",
    )

    restored = serializer.loads_typed(serializer.dumps_typed(proposal))

    assert isinstance(restored, WriteActionProposal)
    assert restored == proposal


def test_checkpoint_serializer_does_not_reconstruct_unlisted_type() -> None:
    serializer = build_checkpoint_serializer()
    original = UntrustedCheckpointType(value="外部类型")

    restored = serializer.loads_typed(serializer.dumps_typed(original))

    assert not isinstance(restored, UntrustedCheckpointType)
    assert restored == {"value": "外部类型"}


def test_checkpoint_serializer_round_trips_nested_memory_type() -> None:
    """长期记忆条目中的枚举也必须在安全允许列表内恢复。"""

    serializer = build_checkpoint_serializer()
    entry = StoredMemoryEntry(
        memory_id=uuid4(),
        memory_type=MemoryType.EMAIL_STYLE,
        memory_key="default",
        version=1,
        value={"tone": "简洁专业"},
        updated_at=datetime(2026, 7, 29, tzinfo=UTC),
    )

    restored = serializer.loads_typed(serializer.dumps_typed(entry))

    assert isinstance(restored, StoredMemoryEntry)
    assert restored.memory_type is MemoryType.EMAIL_STYLE
