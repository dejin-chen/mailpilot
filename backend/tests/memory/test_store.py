"""长期记忆 namespace 和 Graph 加载节点测试。"""

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from app.agent.context import AgentRuntimeContext
from app.agent.nodes.load_memory import LoadMemoryNode
from app.memory.schemas import StoredMemoryEntry
from app.memory.store import memory_store_namespace
from app.models.memory import MemoryType
from langgraph.runtime import Runtime
from langgraph.store.memory import InMemoryStore

NOW = datetime(2026, 7, 25, 9, 0, tzinfo=UTC)


def _context(user_id=None) -> AgentRuntimeContext:
    return AgentRuntimeContext(
        user_id=user_id or uuid4(),
        agent_run_id=uuid4(),
        request_id="load-memory-test",
        user_timezone="Asia/Shanghai",
    )


async def _put_entry(
    store: InMemoryStore,
    *,
    user_id,
    memory_type: MemoryType,
    memory_key: str,
    value: dict[str, object],
    version: int = 1,
) -> None:
    entry = StoredMemoryEntry(
        memory_id=uuid4(),
        memory_type=memory_type,
        memory_key=memory_key,
        version=version,
        value=value,
        updated_at=NOW,
    )
    await store.aput(
        memory_store_namespace(user_id=user_id, memory_type=memory_type),
        memory_key,
        entry.model_dump(mode="json"),
    )


def test_memory_namespace_contains_app_version_user_and_type() -> None:
    user_id = uuid4()

    namespace = memory_store_namespace(
        user_id=user_id,
        memory_type=MemoryType.EMAIL_STYLE,
    )

    assert namespace == (
        "mailpilot",
        "v1",
        "users",
        str(user_id),
        "memories",
        "email_style",
    )


@pytest.mark.asyncio
async def test_load_memory_reads_only_current_user_and_relevant_contact() -> None:
    store = InMemoryStore()
    owner = _context()
    await _put_entry(
        store,
        user_id=owner.user_id,
        memory_type=MemoryType.EMAIL_STYLE,
        memory_key="default",
        value={"tone": "简洁专业", "signature": "张三｜研发部"},
        version=2,
    )
    await _put_entry(
        store,
        user_id=owner.user_id,
        memory_type=MemoryType.CONTACT,
        memory_key="manager@example.com",
        value={
            "email": "manager@example.com",
            "display_name": "王经理",
            "salutation": "王经理，您好",
            "important": True,
        },
    )
    await _put_entry(
        store,
        user_id=owner.user_id,
        memory_type=MemoryType.CONTACT,
        memory_key="other@example.com",
        value={
            "email": "other@example.com",
            "display_name": "不相关联系人",
        },
    )
    state = {"sender": "Manager@Example.com"}

    update = await LoadMemoryNode()(state, Runtime(context=owner, store=store))
    memory = update["memory_context"]

    assert memory.email_style.tone == "简洁专业"
    assert memory.relevant_contact.email == "manager@example.com"
    assert memory.relevant_contact.display_name == "王经理"
    assert all(reference.memory_key != "other@example.com" for reference in memory.references)

    stranger_update = await LoadMemoryNode()(
        state,
        Runtime(context=_context(), store=store),
    )
    stranger_memory = stranger_update["memory_context"]
    assert stranger_memory.email_style is None
    assert stranger_memory.relevant_contact is None
    assert stranger_memory.references == []


@pytest.mark.asyncio
async def test_load_memory_returns_clear_failure_without_store() -> None:
    update = await LoadMemoryNode()(
        {"sender": "manager@example.com"},
        Runtime(context=_context()),
    )

    assert update["run_status"].value == "failed"
    assert update["errors"][-1].code == "AGENT_MEMORY_STORE_MISSING"
