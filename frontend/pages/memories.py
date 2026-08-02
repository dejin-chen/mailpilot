"""用户长期记忆管理页面。"""

import json

import streamlit as st

from frontend.api_client import ApiClientError
from frontend.state import api_client

MEMORY_TYPE_LABELS = {
    "email_style": "邮件风格",
    "calendar_preferences": "日历偏好",
    "contact": "联系人",
}


def render() -> None:
    """查看、创建、修改和删除当前用户自己的长期记忆。"""

    st.title("用户记忆管理")
    client = api_client()
    try:
        items = client.request("GET", "/memories", params={"limit": 100})["items"]
    except ApiClientError as exc:
        st.error(str(exc))
        return

    if items:
        st.dataframe(
            [
                {
                    "类型": MEMORY_TYPE_LABELS.get(item["memory_type"], item["memory_type"]),
                    "Key": item["memory_key"],
                    "版本": item["version"],
                    "来源": item["source_type"],
                    "更新时间": item["updated_at"],
                }
                for item in items
            ],
            use_container_width=True,
            hide_index=True,
        )
        selected = {
            f"{MEMORY_TYPE_LABELS.get(item['memory_type'], item['memory_type'])}"
            f"｜{item['memory_key']}｜v{item['version']}": item
            for item in items
        }
        memory = selected[st.selectbox("选择要修改的记忆", list(selected))]
        updated_value = st.text_area(
            "记忆 JSON",
            value=json.dumps(memory["value"], ensure_ascii=False, indent=2),
            height=200,
        )
        col1, col2 = st.columns(2)
        if col1.button("保存新版本", type="primary", use_container_width=True):
            try:
                client.request(
                    "PUT",
                    f"/memories/{memory['id']}",
                    json_body={
                        "memory_type": memory["memory_type"],
                        "expected_version": memory["version"],
                        "value": json.loads(updated_value),
                    },
                )
                st.success("已创建新的不可变记忆版本")
                st.rerun()
            except (ApiClientError, json.JSONDecodeError) as exc:
                st.error(str(exc))
        if col2.button("删除记忆", use_container_width=True):
            try:
                client.request("DELETE", f"/memories/{memory['id']}")
                st.success("记忆已删除")
                st.rerun()
            except ApiClientError as exc:
                st.error(str(exc))

        with st.expander("查看版本历史"):
            try:
                history = client.request(
                    "GET",
                    f"/memories/{memory['id']}/versions",
                    params={"limit": 100},
                )
                st.json(history["items"], expanded=False)
            except ApiClientError as exc:
                st.error(str(exc))
    else:
        st.info("当前没有长期记忆。")

    with st.expander("主动创建记忆"):
        memory_type = st.selectbox(
            "类型",
            list(MEMORY_TYPE_LABELS),
            format_func=lambda value: MEMORY_TYPE_LABELS[value],
            key="create_memory_type",
        )
        memory_key = st.text_input("Memory Key", value="default")
        default_json = (
            '{"tone": "简洁专业", "signature": "姓名｜部门"}'
            if memory_type == "email_style"
            else "{}"
        )
        value_text = st.text_area("内容 JSON", value=default_json, key="create_memory_value")
        if st.button("创建记忆"):
            try:
                client.request(
                    "POST",
                    "/memories",
                    json_body={
                        "memory_type": memory_type,
                        "memory_key": memory_key,
                        "value": json.loads(value_text),
                    },
                )
                st.success("记忆已创建")
                st.rerun()
            except (ApiClientError, json.JSONDecodeError) as exc:
                st.error(str(exc))
