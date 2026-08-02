"""日历页面。"""

from datetime import datetime, time
from zoneinfo import ZoneInfo

import streamlit as st

from frontend.api_client import ApiClientError
from frontend.state import api_client


def render() -> None:
    """查看日程，并调用后端检查指定时间是否冲突。"""

    st.title("日历")
    client = api_client()
    try:
        items = client.request("GET", "/calendar/events", params={"limit": 100})["items"]
    except ApiClientError as exc:
        st.error(str(exc))
        return

    if items:
        st.dataframe(
            [
                {
                    "标题": item["title"],
                    "开始": item["start_at"],
                    "结束": item["end_at"],
                    "时区": item["timezone"],
                    "状态": item["status"],
                    "地点": item["location"] or "-",
                }
                for item in items
            ],
            use_container_width=True,
            hide_index=True,
        )
    else:
        st.info("当前没有日历事件。")

    st.subheader("检查时间冲突")
    timezone_name = (st.session_state.get("current_user") or {}).get(
        "timezone", "Asia/Shanghai"
    )
    selected_date = st.date_input("日期")
    start_time = st.time_input("开始时间", value=time(9, 0))
    end_time = st.time_input("结束时间", value=time(10, 0))
    if st.button("检查可用性"):
        timezone = ZoneInfo(timezone_name)
        start_at = datetime.combine(selected_date, start_time, timezone)
        end_at = datetime.combine(selected_date, end_time, timezone)
        try:
            result = client.request(
                "GET",
                "/calendar/availability",
                params={
                    "start_at": start_at.isoformat(),
                    "end_at": end_at.isoformat(),
                },
            )
            if result["available"]:
                st.success("该时间段可用")
            else:
                st.warning("该时间段存在冲突")
                st.json(result["conflicts"], expanded=True)
        except ApiClientError as exc:
            st.error(str(exc))
