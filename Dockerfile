FROM ghcr.io/astral-sh/uv:0.9.30 AS uv

FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/app/.venv/bin:$PATH"

WORKDIR /app

RUN addgroup --system mailpilot \
    && adduser --system --ingroup mailpilot mailpilot

COPY --from=uv /uv /uvx /bin/
COPY pyproject.toml uv.lock README.md ./

# 第三方依赖只由锁文件决定，业务代码变化时可以复用这一层。
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-install-project

COPY backend ./backend
COPY mcp_servers ./mcp_servers
COPY frontend ./frontend
COPY .streamlit ./.streamlit

# 依赖已经安装，本层只补装当前项目。
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-editable --reinstall-package mailpilot

USER mailpilot

EXPOSE 8000 8501

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
