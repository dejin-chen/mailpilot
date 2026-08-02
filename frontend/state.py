"""Streamlit Session State 的最小封装。"""

import streamlit as st

from frontend.api_client import MailPilotApiClient


def initialize_session_state() -> None:
    """为首次打开页面准备稳定的默认状态。"""

    defaults = {
        "access_token": None,
        "current_user": None,
        "selected_email_thread_id": None,
        "selected_run_id": None,
        "last_event_sequence": 0,
    }
    for key, value in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = value


def api_client() -> MailPilotApiClient:
    """使用当前登录 JWT 创建短生命周期 API 客户端。"""

    return MailPilotApiClient(access_token=st.session_state.get("access_token"))


def clear_login_state() -> None:
    """退出登录时清除身份和页面选择，不保留 JWT。"""

    for key in (
        "access_token",
        "current_user",
        "selected_email_thread_id",
        "selected_run_id",
        "last_event_sequence",
    ):
        st.session_state[key] = None if key != "last_event_sequence" else 0
