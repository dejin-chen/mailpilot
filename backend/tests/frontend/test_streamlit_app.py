"""Streamlit 应用入口烟雾测试。"""

from pathlib import Path

from streamlit.testing.v1 import AppTest


def test_login_page_renders_without_runtime_exception() -> None:
    app_file = Path(__file__).parents[3] / "frontend" / "app.py"

    app = AppTest.from_file(str(app_file)).run(timeout=20)

    assert not list(app.exception)
    assert [item.value for item in app.title] == ["MailPilot 登录"]
    assert [item.label for item in app.text_input] == ["邮箱", "密码"]
    assert [item.label for item in app.button] == ["登录"]


def test_authenticated_navigation_uses_unique_page_paths() -> None:
    app_file = Path(__file__).parents[3] / "frontend" / "app.py"
    app = AppTest.from_file(str(app_file))
    app.session_state["access_token"] = "test-token"
    app.session_state["current_user"] = {
        "email": "test@example.com",
        "full_name": "前端测试用户",
        "timezone": "Asia/Shanghai",
    }

    app.run(timeout=20)

    assert not list(app.exception)
    assert [item.value for item in app.title] == ["邮件处理台"]
