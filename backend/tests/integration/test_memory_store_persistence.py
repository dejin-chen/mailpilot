"""LangGraph PostgreSQL Store 跨连接持久化与 namespace 隔离测试。"""

import os
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from app.core.config import Settings
from app.memory.schemas import StoredMemoryEntry
from app.memory.store import memory_store_namespace, open_postgres_store
from app.models.memory import MemoryType

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not os.getenv("TEST_DATABASE_URL"),
        reason="未配置 TEST_DATABASE_URL，跳过 PostgreSQL Store 持久化测试",
    ),
]


def _settings() -> Settings:
    return Settings(
        environment="test",
        database_url=os.environ["TEST_DATABASE_URL"],
        database_pool_size=2,
        redis_url="redis://localhost:6379/15",
        jwt_secret_key="memory-store-persistence-secret-at-least-32-characters",
    )


@pytest.mark.asyncio
async def test_postgres_store_survives_reopen_and_isolates_users() -> None:
    owner_id = uuid4()
    stranger_id = uuid4()
    namespace = memory_store_namespace(
        user_id=owner_id,
        memory_type=MemoryType.EMAIL_STYLE,
    )
    entry = StoredMemoryEntry(
        memory_id=uuid4(),
        memory_type=MemoryType.EMAIL_STYLE,
        memory_key="default",
        version=4,
        value={"tone": "跨连接仍然存在"},
        updated_at=datetime(2026, 7, 25, 10, 0, tzinfo=UTC),
    )

    async with open_postgres_store(_settings(), setup=True) as first_store:
        await first_store.aput(
            namespace,
            "default",
            entry.model_dump(mode="json"),
            index=False,
        )

    async with open_postgres_store(_settings()) as reopened_store:
        restored = await reopened_store.aget(namespace, "default")
        stranger_item = await reopened_store.aget(
            memory_store_namespace(
                user_id=stranger_id,
                memory_type=MemoryType.EMAIL_STYLE,
            ),
            "default",
        )
        assert restored is not None
        assert StoredMemoryEntry.model_validate(restored.value).version == 4
        assert stranger_item is None
        await reopened_store.adelete(namespace, "default")
