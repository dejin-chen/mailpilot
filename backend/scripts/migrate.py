"""依次升级业务表和初始化 LangGraph 官方存档表。"""

import asyncio
import sys
from pathlib import Path

from alembic import command
from alembic.config import Config
from app.agent.checkpoint import open_postgres_checkpointer
from app.memory.store import open_postgres_store

PROJECT_ROOT = Path(__file__).resolve().parents[2]


async def setup_langgraph_persistence() -> None:
    """幂等初始化 LangGraph Checkpointer 和长期 Store 自管表。"""

    async with open_postgres_checkpointer(setup=True):
        pass
    async with open_postgres_store(setup=True):
        pass


def main() -> None:
    """先执行 Alembic，再执行 Checkpointer setup，任一步失败都让任务失败退出。"""

    alembic_config = Config(PROJECT_ROOT / "backend" / "alembic.ini")
    command.upgrade(alembic_config, "head")
    loop_factory = asyncio.SelectorEventLoop if sys.platform == "win32" else None
    asyncio.run(setup_langgraph_persistence(), loop_factory=loop_factory)
    print("业务迁移、LangGraph Checkpointer 和长期 Store 初始化完成")


if __name__ == "__main__":
    main()
