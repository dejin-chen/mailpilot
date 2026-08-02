"""通过 LangChain MCP Adapter 调用两个 MailPilot MCP Server。"""

import argparse
import asyncio
import json
import sys
from typing import Any

from app.core.config import get_settings
from app.db.session import AsyncSessionFactory, close_database
from app.integrations.mcp.client import MailPilotMcpClient
from app.repositories.user import UserRepository


def parse_args() -> argparse.Namespace:
    """读取演示参数，默认搜索包含 MailPilot 的邮件。"""

    parser = argparse.ArgumentParser(description="调用 MailPilot MCP 只读工具")
    parser.add_argument("--query", default="MailPilot", help="邮件搜索关键词")
    return parser.parse_args()


def serialize_result(result: Any) -> str:
    """把 LangChain 返回的标准内容块格式化为便于阅读的中文终端输出。"""

    return json.dumps(result, ensure_ascii=False, indent=2, default=str)


async def run_demo(query: str) -> None:
    """发现工具，并以演示用户身份调用邮件搜索工具。"""

    settings = get_settings()
    async with AsyncSessionFactory() as session:
        user = await UserRepository(session).get_by_email(settings.demo_user_email)
        if user is None:
            message = "未找到演示用户，请先运行 backend/scripts/seed_demo.py"
            raise RuntimeError(message)

        client = MailPilotMcpClient(
            settings=settings,
            user_id=user.id,
            request_id="local-mcp-demo",
        )
        tools = await client.discover_tools()
        print("Agent 当前允许发现的工具：")
        print("、".join(tool.name for tool in tools))

        result = await client.invoke_tool(
            "search_emails",
            {"query": query, "offset": 0, "limit": 5},
        )
        print("\n邮件搜索结果：")
        print(serialize_result(result))


async def run(query: str) -> None:
    """运行演示并在结束时释放数据库连接池。"""

    try:
        await run_demo(query)
    finally:
        await close_database()


def main() -> None:
    """使用兼容 Windows 的事件循环启动异步演示。"""

    args = parse_args()
    loop_factory = asyncio.SelectorEventLoop if sys.platform == "win32" else None
    asyncio.run(run(args.query), loop_factory=loop_factory)


if __name__ == "__main__":
    main()
