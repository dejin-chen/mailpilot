"""不同数据库客户端之间的 PostgreSQL URL 转换。"""

from sqlalchemy.engine import make_url


def build_psycopg_database_url(database_url: str) -> str:
    """把 SQLAlchemy URL 转成 LangGraph/psycopg 可直接使用的 URL。"""

    parsed = make_url(database_url)
    return parsed.set(drivername="postgresql").render_as_string(hide_password=False)
