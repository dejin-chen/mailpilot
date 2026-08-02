"""邮件处理台。"""

from datetime import UTC, datetime
from uuid import uuid4

import streamlit as st

from frontend.api_client import ApiClientError
from frontend.state import api_client


def render() -> None:
    """浏览、导入测试邮件并启动后台 Agent。"""

    st.title("邮件处理台")
    client = api_client()
    try:
        page = client.request("GET", "/emails", params={"limit": 100})
        items = page["items"]
    except ApiClientError as exc:
        st.error(str(exc))
        return

    if items:
        labels = {
            f"{item['subject'] or '（无主题）'}｜{item['last_message_at']}": item["id"]
            for item in items
        }
        selected_label = st.selectbox("选择邮件线程", list(labels))
        selected_id = labels[selected_label]
        st.session_state["selected_email_thread_id"] = selected_id
        columns = st.columns(2)
        if columns[0].button("查看详情", use_container_width=True):
            st.info("已选中邮件，请在左侧进入“邮件详情与分析”。")
        if columns[1].button("启动 Agent 处理", type="primary", use_container_width=True):
            try:
                started = client.request("POST", f"/emails/{selected_id}/process")
                st.session_state["selected_run_id"] = started["run"]["id"]
                st.session_state["last_event_sequence"] = 0
                st.success(
                    f"已进入后台处理，运行编号：{started['run']['id']}。"
                    "可前往“执行轨迹”实时查看。"
                )
            except ApiClientError as exc:
                st.error(str(exc))
        st.dataframe(
            [
                {
                    "主题": item["subject"] or "（无主题）",
                    "参与人": "、".join(item["participants"]),
                    "状态": item["status"],
                    "最后邮件": item["last_message_at"],
                }
                for item in items
            ],
            use_container_width=True,
            hide_index=True,
        )
    else:
        st.info("邮箱中还没有测试邮件，可以在下方导入一封。")

    with st.expander("导入测试邮件"):
        with st.form("import_email_form"):
            subject = st.text_input("主题", value="项目进度确认")
            sender = st.text_input("发件人", value="manager@example.com")
            recipient = st.text_input(
                "收件人",
                value=(st.session_state.get("current_user") or {}).get(
                    "email", "demo@example.com"
                ),
            )
            body_text = st.text_area("邮件正文", value="请回复确认今天能否完成。")
            submitted = st.form_submit_button("导入")
        if submitted:
            external_id = str(uuid4())
            try:
                client.request(
                    "POST",
                    "/emails/import",
                    json_body={
                        "provider": "local",
                        "thread_external_id": f"ui-thread-{external_id}",
                        "message_external_id": f"ui-message-{external_id}",
                        "subject": subject,
                        "sender": sender,
                        "recipients": [recipient],
                        "cc": [],
                        "body_text": body_text,
                        "headers": {},
                        "sent_at": datetime.now(UTC).isoformat(),
                    },
                )
                st.success("测试邮件已导入")
                st.rerun()
            except ApiClientError as exc:
                st.error(str(exc))
