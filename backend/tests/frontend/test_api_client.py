"""Streamlit API 客户端的 SSE 解析测试。"""

from frontend.api_client import MailPilotApiClient


def test_parse_sse_lines_returns_event_and_heartbeat() -> None:
    lines = iter(
        [
            ": heartbeat",
            "",
            "id: 4",
            "event: node_completed",
            'data: {"sequence":4,"node_name":"classify_email"}',
            "",
        ]
    )

    events = list(MailPilotApiClient._parse_sse_lines(lines))

    assert events[0]["event"] == "heartbeat"
    assert events[1] == {
        "event": "node_completed",
        "id": "4",
        "data": {"sequence": 4, "node_name": "classify_email"},
    }
