"""业务长期记忆同步到 LangGraph Store 的单元测试。"""

import asyncio
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from app.memory.schemas import StoredMemoryEntry
from app.memory.store import memory_store_namespace
from app.models.memory import MemorySourceType, MemoryType
from app.repositories.audit import AuditRepository
from app.repositories.memory import MemoryRecord, MemoryRepository
from app.services.exceptions import AgentMemoryStoreUnavailableError
from app.services.memory_store import MemoryStoreSyncService
from langgraph.store.base import BaseStore
from sqlalchemy.ext.asyncio import AsyncSession

NOW = datetime(2026, 7, 25, 9, 0, tzinfo=UTC)


def _record(*, user_id, version: int = 2) -> MemoryRecord:
    return MemoryRecord(
        id=uuid4(),
        user_id=user_id,
        memory_type=MemoryType.EMAIL_STYLE,
        memory_key="default",
        value={"tone": "简洁专业"},
        version=version,
        source_type=MemorySourceType.USER_API_EDIT,
        source_reference_type=None,
        source_reference_id=None,
        created_at=NOW,
        updated_at=NOW,
    )


@pytest.mark.asyncio
async def test_sync_upserts_current_deletes_stale_and_writes_safe_audit() -> None:
    session = AsyncMock(spec=AsyncSession)
    store = AsyncMock(spec=BaseStore)
    memories = AsyncMock(spec=MemoryRepository)
    audits = AsyncMock(spec=AuditRepository)
    user_id = uuid4()
    record = _record(user_id=user_id)
    memories.list_all_current.return_value = [record]
    store.asearch.side_effect = [
        [SimpleNamespace(key="stale", value={"old": True})],
        [],
        [],
    ]
    service = MemoryStoreSyncService(session, store, memories, audits)

    result = await service.sync_user_memories(
        user_id=user_id,
        actor_user_id=user_id,
        agent_run_id=uuid4(),
        request_id="memory-sync-001",
    )

    assert len(result) == 1
    assert result[0].version == 2
    store.adelete.assert_awaited_once_with(
        memory_store_namespace(
            user_id=user_id,
            memory_type=MemoryType.EMAIL_STYLE,
        ),
        "stale",
    )
    put_payload = store.aput.await_args.args[2]
    assert StoredMemoryEntry.model_validate(put_payload).memory_id == record.id
    assert store.aput.await_args.kwargs["index"] is False
    audit = audits.add.await_args.args[0]
    assert audit.action == "memory.loaded_for_agent"
    assert audit.details["memory_count"] == 1
    assert "value" not in audit.details
    assert "简洁专业" not in str(audit.details)
    session.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_sync_maps_store_failure_to_service_unavailable() -> None:
    session = AsyncMock(spec=AsyncSession)
    store = AsyncMock(spec=BaseStore)
    memories = AsyncMock(spec=MemoryRepository)
    audits = AsyncMock(spec=AuditRepository)
    memories.list_all_current.return_value = []
    store.asearch.side_effect = RuntimeError("store down")
    service = MemoryStoreSyncService(session, store, memories, audits)

    with pytest.raises(AgentMemoryStoreUnavailableError):
        await service.sync_user_memories(
            user_id=uuid4(),
            actor_user_id=uuid4(),
            agent_run_id=uuid4(),
        )

    session.rollback.assert_awaited_once()
    session.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_sync_times_out_instead_of_waiting_forever() -> None:
    """Store 连接失效时必须在有限时间内结束，不能让 Agent 永久运行。"""

    session = AsyncMock(spec=AsyncSession)
    store = AsyncMock(spec=BaseStore)
    memories = AsyncMock(spec=MemoryRepository)
    audits = AsyncMock(spec=AuditRepository)
    memories.list_all_current.return_value = []

    async def never_returns(*args, **kwargs):
        del args, kwargs
        await asyncio.Event().wait()

    store.asearch.side_effect = never_returns
    service = MemoryStoreSyncService(
        session,
        store,
        memories,
        audits,
        timeout_seconds=0.01,
    )

    with pytest.raises(AgentMemoryStoreUnavailableError):
        await service.sync_user_memories(
            user_id=uuid4(),
            actor_user_id=uuid4(),
            agent_run_id=uuid4(),
        )

    session.rollback.assert_awaited_once()
    session.commit.assert_not_awaited()
