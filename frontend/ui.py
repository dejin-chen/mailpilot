"""多个 Streamlit 页面复用的中文展示组件。"""

from typing import Any

import streamlit as st

STATUS_LABELS = {
    "pending": "等待执行",
    "running": "执行中",
    "waiting_approval": "等待审批",
    "completed": "已完成",
    "failed": "失败",
    "cancelled": "已取消",
}

EVENT_LABELS = {
    "run_created": "创建运行",
    "run_started": "开始执行",
    "run_resumed": "审批后恢复",
    "node_completed": "节点完成",
    "approval_required": "等待人工审批",
    "run_completed": "执行完成",
    "run_failed": "执行失败",
    "run_cancelled": "执行取消",
}


def show_api_error(exc: Exception) -> None:
    """使用统一样式展示 API 错误。"""

    st.error(str(exc))


def render_run_summary(run: dict[str, Any]) -> None:
    """展示 AgentRun 的状态、当前节点、Token 和耗时相关时间。"""

    columns = st.columns(4)
    columns[0].metric("状态", STATUS_LABELS.get(run.get("status"), run.get("status", "-")))
    columns[1].metric("当前节点", run.get("current_node") or "-")
    columns[2].metric("Token", int(run.get("total_tokens") or 0))
    columns[3].metric("运行编号", str(run.get("id", "-"))[:8])
    if run.get("error_message"):
        st.error(f"{run.get('error_code', 'ERROR')}：{run['error_message']}")


def render_agent_result(result: dict[str, Any]) -> None:
    """把结构化分析、计划、草稿和最终结果分区展示。"""

    classification = result.get("classification")
    if classification:
        st.subheader("邮件判断")
        st.json(classification, expanded=True)
    intent = result.get("intent")
    if intent:
        st.subheader("任务与会议意图")
        st.json(intent, expanded=False)
    plan = result.get("plan")
    if plan:
        st.subheader("Agent 计划")
        st.json(plan, expanded=True)
    draft = result.get("draft")
    if draft:
        st.subheader("回复草稿")
        st.text_input("主题", value=draft.get("subject", ""), disabled=True)
        st.text_area("正文", value=draft.get("body_text", ""), height=220, disabled=True)
    if result.get("final_result"):
        st.subheader("最终结果")
        st.json(result["final_result"], expanded=True)


def render_event_table(events: list[dict[str, Any]]) -> None:
    """按 sequence 展示执行轨迹，不暴露原始 Prompt。"""

    if not events:
        st.info("当前还没有执行事件。")
        return
    rows = []
    for event in events:
        payload = event.get("payload") or {}
        rows.append(
            {
                "序号": event.get("sequence"),
                "事件": EVENT_LABELS.get(event.get("event_type"), event.get("event_type")),
                "节点": event.get("node_name") or "-",
                "状态": STATUS_LABELS.get(payload.get("status"), payload.get("status") or "-"),
                "更新时间": event.get("created_at"),
            }
        )
    st.dataframe(rows, use_container_width=True, hide_index=True)
