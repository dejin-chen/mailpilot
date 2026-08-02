"""LangGraph PostgreSQL 长期 Store 的命名空间和生命周期。"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from math import ceil
from uuid import UUID

from langgraph.store.postgres import AsyncPostgresStore
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

from app.core.config import Settings, get_settings
from app.core.database_urls import build_psycopg_database_url
from app.models.memory import MemoryType

STORE_NAMESPACE_ROOT = ("mailpilot", "v1", "users")


def memory_store_namespace(
    *,
    user_id: UUID,
    memory_type: MemoryType,
) -> tuple[str, ...]:
    """以应用版本、用户和记忆类型组成不会串数据的明确 namespace。"""

    return (
        *STORE_NAMESPACE_ROOT,
        str(user_id),
        "memories",
        memory_type.value,
    )


@asynccontextmanager
async def open_postgres_store(
    settings: Settings | None = None,
    *,
    setup: bool = False,
) -> AsyncIterator[AsyncPostgresStore]:
    """打开带失效连接检查的长期 Store 连接池，并在退出时可靠释放。"""

    current_settings = settings or get_settings()
    timeout_seconds = current_settings.agent_store_timeout_seconds
    pool = AsyncConnectionPool(
        conninfo=build_psycopg_database_url(current_settings.database_url),
        kwargs={
            "autocommit": True,
            "prepare_threshold": 0,
            "row_factory": dict_row,
            "connect_timeout": max(1, ceil(timeout_seconds)),
        },
        min_size=1,
        max_size=max(1, current_settings.database_pool_size),
        open=False,
        check=AsyncConnectionPool.check_connection,
        timeout=timeout_seconds,
        reconnect_timeout=timeout_seconds,
        name="mailpilot-langgraph-store",
    )
    await pool.open(wait=True, timeout=timeout_seconds)
    try:
        store = AsyncPostgresStore(conn=pool)
        if setup:
            await store.setup()
        yield store
    finally:
        await pool.close()
