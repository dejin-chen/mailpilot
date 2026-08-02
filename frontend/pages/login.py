"""登录页。"""

import streamlit as st

from frontend.api_client import ApiClientError, MailPilotApiClient


def render() -> None:
    """使用后端 JWT 登录，不在浏览器代码中保存密码。"""

    st.title("MailPilot 登录")
    st.caption("企业邮件与日程协同 Agent")
    with st.form("login_form"):
        email = st.text_input("邮箱", placeholder="demo@example.com")
        password = st.text_input("密码", type="password")
        submitted = st.form_submit_button("登录", type="primary", use_container_width=True)
    if not submitted:
        return
    try:
        token = MailPilotApiClient().login(email=email, password=password)
        st.session_state["access_token"] = token["access_token"]
        current_user = MailPilotApiClient(
            access_token=token["access_token"]
        ).request("GET", "/users/me")
        st.session_state["current_user"] = current_user
        st.success("登录成功")
        st.rerun()
    except ApiClientError as exc:
        st.error(str(exc))
