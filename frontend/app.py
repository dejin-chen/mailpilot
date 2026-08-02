"""MailPilot Streamlit 中文工作台入口。"""

import streamlit as st

from frontend.pages import (
    approvals,
    calendar_page,
    login,
    mail_console,
    mail_detail,
    memories,
    trace,
)
from frontend.state import clear_login_state, initialize_session_state

st.set_page_config(
    page_title="MailPilot 工作台",
    page_icon="✉️",
    layout="wide",
)
initialize_session_state()

if st.session_state.get("access_token"):
    current_user = st.session_state.get("current_user") or {}
    st.sidebar.caption(
        f"当前用户：{current_user.get('full_name') or current_user.get('email') or '已登录用户'}"
    )
    if st.sidebar.button("退出登录", use_container_width=True):
        clear_login_state()
        st.rerun()
    navigation = st.navigation(
        {
            "邮件": [
                st.Page(
                    mail_console.render,
                    title="邮件处理台",
                    icon=":material/inbox:",
                    url_path="mail-console",
                    default=True,
                ),
                st.Page(
                    mail_detail.render,
                    title="邮件详情与分析",
                    icon=":material/mail:",
                    url_path="mail-detail",
                ),
            ],
            "协同": [
                st.Page(
                    approvals.render,
                    title="审批中心",
                    icon=":material/approval:",
                    url_path="approvals",
                ),
                st.Page(
                    calendar_page.render,
                    title="日历",
                    icon=":material/calendar_month:",
                    url_path="calendar",
                ),
            ],
            "Agent": [
                st.Page(
                    trace.render,
                    title="执行轨迹",
                    icon=":material/route:",
                    url_path="agent-trace",
                ),
                st.Page(
                    memories.render,
                    title="用户记忆",
                    icon=":material/memory:",
                    url_path="memories",
                ),
            ],
        }
    )
else:
    navigation = st.navigation(
        [
            st.Page(
                login.render,
                title="登录",
                icon=":material/login:",
                url_path="login",
                default=True,
            )
        ]
    )

navigation.run()
