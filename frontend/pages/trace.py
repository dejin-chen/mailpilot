"""Agent 执行轨迹页面。"""

import streamlit as st

from frontend.api_client import ApiClientError
from frontend.state import api_client
from frontend.ui import render_agent_result, render_event_table, render_run_summary


def render() -> None:
    """查看持久化轨迹，并通过 SSE 接收尚未到达页面的新事件。"""

    st.title("Agent 执行轨迹")
    client = api_client()
    try:
        runs = client.request("GET", "/agent-runs", params={"limit": 100})["items"]
    except ApiClientError as exc:
        st.error(str(exc))
        return
    if not runs:
        st.info("还没有 Agent 执行记录。")
        return

    labels = {
        f"{run['created_at']}｜{run['status']}｜{run['id'][:8]}": run["id"]
        for run in runs
    }
    remembered = st.session_state.get("selected_run_id")
    option_labels = list(labels)
    default_index = next(
        (index for index, label in enumerate(option_labels) if labels[label] == remembered),
        0,
    )
    selected_label = st.selectbox("选择运行", option_labels, index=default_index)
    run_id = labels[selected_label]
    st.session_state["selected_run_id"] = run_id

    try:
        run = client.request("GET", f"/agent-runs/{run_id}")
        history = client.request(
            "GET",
            f"/agent-runs/{run_id}/events/history",
            params={"after": 0, "limit": 500},
        )
    except ApiClientError as exc:
        st.error(str(exc))
        return

    render_run_summary(run)
    events = history["items"]
    render_event_table(events)
    render_agent_result(run.get("result") or {})
    last_sequence = int(history.get("next_after") or 0)
    st.session_state["last_event_sequence"] = last_sequence

    if run["status"] in {"pending", "running"}:
        status_placeholder = st.empty()
        table_placeholder = st.empty()
        status_placeholder.caption("正在自动连接实时轨迹……")
        try:
            for message in client.stream_events(run_id=run_id, after=last_sequence):
                last_sequence, status_text, finished = _apply_stream_message(
                    events=events,
                    last_sequence=last_sequence,
                    message=message,
                )
                st.session_state["last_event_sequence"] = last_sequence
                if finished:
                    status_placeholder.success(status_text)
                    st.rerun()
                status_placeholder.caption(status_text)
                if message["event"] == "heartbeat":
                    continue
                with table_placeholder.container():
                    render_event_table(events)
        except ApiClientError as exc:
            status_placeholder.warning(str(exc))
            if st.button("重新连接实时轨迹", type="primary"):
                st.rerun()


def _apply_stream_message(
    *,
    events: list[dict],
    last_sequence: int,
    message: dict,
) -> tuple[int, str, bool]:
    """合并一条 SSE 消息，并告诉页面是否需要刷新最终快照。"""

    event_type = str(message.get("event") or "message")
    data = message.get("data")
    if event_type == "heartbeat":
        return last_sequence, "连接正常，正在等待新事件……", False
    if event_type == "stream_closed":
        terminal_sequence = (
            int(data.get("last_sequence") or 0) if isinstance(data, dict) else 0
        )
        return (
            max(last_sequence, terminal_sequence),
            "Agent 执行已结束，正在刷新最终结果……",
            True,
        )
    event = data if isinstance(data, dict) else {}
    events.append(event)
    next_sequence = max(last_sequence, int(event.get("sequence") or 0))
    return next_sequence, f"收到事件 {next_sequence}：{event_type}", False
