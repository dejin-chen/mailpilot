"""初始化 LangGraph 官方 PostgreSQL Checkpointer 的内部表。"""

import asyncio
import sys

from app.agent.checkpoint import open_postgres_checkpointer


async def main() -> None:
    """执行官方幂等 setup；不使用 SQLAlchemy create_all。"""

    async with open_postgres_checkpointer(setup=True):
        print("LangGraph Checkpointer 初始化完成")


if __name__ == "__main__":
    loop_factory = asyncio.SelectorEventLoop if sys.platform == "win32" else None
    asyncio.run(main(), loop_factory=loop_factory)
