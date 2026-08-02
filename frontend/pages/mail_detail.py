"""邮件详情与 Agent 分析结果页。"""

import streamlit as st

from frontend.api_client import ApiClientError
from frontend.state import api_client
from frontend.ui import render_agent_result, render_run_summary


def render() -> None:
    """显示原始邮件线程，并关联该线程最近一次 AgentRun。"""

    st.title("邮件详情与 Agent 分析")
    client = api_client()
    try:
        emails = client.request("GET", "/emails", params={"limit": 100})["items"]
    except ApiClientError as exc:
        st.error(str(exc))
        return
    if not emails:
        st.info("暂无邮件。")
        return

    options = {f"{item['subject']}｜{item['id'][:8]}": item["id"] for item in emails}
    remembered = st.session_state.get("selected_email_thread_id")
    option_labels = list(options)
    default_index = next(
        (index for index, label in enumerate(option_labels) if options[label] == remembered),
        0,
    )
    label = st.selectbox("邮件线程", option_labels, index=default_index)
    thread_id = options[label]
    st.session_state["selected_email_thread_id"] = thread_id
    try:
        thread = client.request("GET", f"/emails/{thread_id}")
        runs = client.request("GET", "/agent-runs", params={"limit": 100})["items"]
    except ApiClientError as exc:
        st.error(str(exc))
        return

    st.subheader(thread["subject"] or "（无主题）")
    for message in thread["messages"]:
        with st.container(border=True):
            st.caption(f"{message['sender']} · {message['sent_at']}")
            st.write(message["body_text"])

    related = [run for run in runs if run["email_thread_id"] == thread_id]
    if not related:
        st.info("这封邮件还没有 Agent 分析结果。")
        return
    run = related[0]
    render_run_summary(run)
    render_agent_result(run.get("result") or {})
