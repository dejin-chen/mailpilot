"""执行轨迹页面的实时消息合并测试。"""

from frontend.pages.trace import _apply_stream_message


def test_node_event_updates_sequence_and_rows() -> None:
    events: list[dict] = []

    sequence, message, finished = _apply_stream_message(
        events=events,
        last_sequence=3,
        message={
            "event": "node_completed",
            "data": {"sequence": 4, "node_name": "load_memory"},
        },
    )

    assert sequence == 4
    assert events == [{"sequence": 4, "node_name": "load_memory"}]
    assert "node_completed" in message
    assert finished is False


def test_stream_closed_requests_final_snapshot_refresh() -> None:
    events: list[dict] = []

    sequence, message, finished = _apply_stream_message(
        events=events,
        last_sequence=4,
        message={
            "event": "stream_closed",
            "data": {"status": "completed", "last_sequence": 7},
        },
    )

    assert sequence == 7
    assert events == []
    assert "刷新最终结果" in message
    assert finished is True
