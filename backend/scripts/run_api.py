"""以 Windows psycopg 兼容的事件循环启动 FastAPI。"""

import asyncio
import os
import sys

import uvicorn


def main() -> None:
    """Windows 使用 SelectorEventLoop，Linux/Docker 保持平台默认实现。"""

    config = uvicorn.Config(
        "app.main:app",
        host=os.getenv("BACKEND_HOST", "0.0.0.0"),
        port=int(os.getenv("BACKEND_PORT", "8000")),
    )
    server = uvicorn.Server(config)
    loop_factory = asyncio.SelectorEventLoop if sys.platform == "win32" else None
    asyncio.run(server.serve(), loop_factory=loop_factory)


if __name__ == "__main__":
    main()
