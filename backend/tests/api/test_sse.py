"""SSE 编码和断线续传游标单元测试。"""

import json

from app.api.sse import encode_sse_event
from app.api.v1.agent_runs import _resume_cursor


def test_encode_sse_event_contains_id_type_and_unicode_json() -> None:
    encoded = encode_sse_event(
        event="node_completed",
        event_id="7",
        data={"message": "分类完成"},
    )

    assert encoded.startswith("id: 7\nevent: node_completed\n")
    data_line = next(line for line in encoded.splitlines() if line.startswith("data: "))
    assert json.loads(data_line.removeprefix("data: ")) == {"message": "分类完成"}
    assert encoded.endswith("\n\n")


def test_resume_cursor_prefers_greater_valid_position() -> None:
    assert _resume_cursor(after=3, last_event_id="8") == 8
    assert _resume_cursor(after=3, last_event_id="invalid") == 3
    assert _resume_cursor(after=3, last_event_id="-1") == 3
