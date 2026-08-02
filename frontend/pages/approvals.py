"""人工审批中心。"""

import json

import streamlit as st

from frontend.api_client import ApiClientError
from frontend.state import api_client


def render() -> None:
    """查看审批依据，并支持接受、拒绝、修改后接受和反馈重生成。"""

    st.title("审批中心")
    client = api_client()
    try:
        approvals = client.request("GET", "/approvals", params={"limit": 100})["items"]
    except ApiClientError as exc:
        st.error(str(exc))
        return
    if not approvals:
        st.info("暂无审批请求。")
        return

    labels = {
        f"{item['action']}｜v{item['version']}｜{item['status']}｜{item['id'][:8]}": item
        for item in approvals
    }
    selected = labels[st.selectbox("选择审批单", list(labels))]
    st.write(f"状态：`{selected['status']}`")
    st.write(f"关联运行：`{selected['agent_run_id']}`")
    st.subheader("Agent 准备调用的工具参数")
    st.json(selected["effective_arguments"], expanded=True)

    try:
        run = client.request("GET", f"/agent-runs/{selected['agent_run_id']}")
        with st.expander("查看 Agent 计划"):
            st.json((run.get("result") or {}).get("plan") or {}, expanded=True)
        with st.expander("查看原始邮件"):
            thread = client.request("GET", f"/emails/{run['email_thread_id']}")
            for message in thread["messages"]:
                st.caption(f"{message['sender']} · {message['sent_at']}")
                st.write(message["body_text"])
    except ApiClientError as exc:
        st.warning(str(exc))

    if selected["status"] != "pending":
        st.info("该审批已经处理，不能重复执行。")
        return

    col1, col2 = st.columns(2)
    if col1.button("接受", type="primary", use_container_width=True):
        _submit(client, f"/approvals/{selected['id']}/approve", {})
    reject_reason = st.text_input("拒绝原因", key=f"reject-{selected['id']}")
    if col2.button("拒绝", use_container_width=True):
        _submit(
            client,
            f"/approvals/{selected['id']}/reject",
            {"feedback": reject_reason or None},
        )

    st.subheader("修改参数后接受")
    modified = st.text_area(
        "修改后的 JSON 参数",
        value=json.dumps(selected["effective_arguments"], ensure_ascii=False, indent=2),
        height=220,
    )
    if st.button("校验修改并接受", use_container_width=True):
        try:
            arguments = json.loads(modified)
        except json.JSONDecodeError:
            st.error("修改参数不是合法 JSON")
        else:
            _submit(
                client,
                f"/approvals/{selected['id']}/approve-with-modifications",
                {"modified_arguments": arguments},
            )

    feedback = st.text_area(
        "反馈并要求重新生成",
        placeholder="例如：语气更简洁，并记住以后都使用这种风格",
    )
    if st.button("提交反馈并重新生成", use_container_width=True):
        _submit(
            client,
            f"/approvals/{selected['id']}/request-regeneration",
            {"feedback": feedback},
        )


def _submit(client, path: str, payload: dict) -> None:
    try:
        client.request("POST", path, json_body=payload)
        st.success("审批决定已保存，工作流已经恢复或安全复用已有结果")
        st.rerun()
    except ApiClientError as exc:
        st.error(str(exc))
