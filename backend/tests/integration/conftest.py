"""PostgreSQL 集成测试 Fixture。"""

import asyncio
import os
from collections.abc import AsyncIterator, Callable

import pytest
from app.db.session import get_db_session
from app.main import app
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool


def pytest_asyncio_loop_factories(
    config: pytest.Config,
    item: pytest.Item,
) -> dict[str, Callable[[], asyncio.AbstractEventLoop]]:
    """为集成测试提供 psycopg 兼容的事件循环工厂。"""

    del config, item
    return {"selector": asyncio.SelectorEventLoop}


@pytest.fixture
async def integration_session() -> AsyncIterator[AsyncSession]:
    """每条测试使用独立外层事务，结束后统一回滚。"""

    database_url = os.getenv("TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("未配置 TEST_DATABASE_URL，跳过 PostgreSQL 集成测试")

    engine = create_async_engine(database_url, poolclass=NullPool)
    async with engine.connect() as connection:
        outer_transaction = await connection.begin()
        session = AsyncSession(
            bind=connection,
            expire_on_commit=False,
            join_transaction_mode="create_savepoint",
        )
        try:
            yield session
        finally:
            await session.close()
            if outer_transaction.is_active:
                await outer_transaction.rollback()
    await engine.dispose()


@pytest.fixture
async def integration_client(
    integration_session: AsyncSession,
) -> AsyncIterator[AsyncClient]:
    """让 FastAPI 使用真实测试 Session，而不是应用默认数据库。"""

    async def override_db_session() -> AsyncIterator[AsyncSession]:
        yield integration_session

    app.dependency_overrides[get_db_session] = override_db_session
    transport = ASGITransport(app=app)
    try:
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            yield client
    finally:
        app.dependency_overrides.pop(get_db_session, None)
