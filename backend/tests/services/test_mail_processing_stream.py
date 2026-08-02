"""LangGraph updates 事件解析单元测试。"""

from app.services.mail_processing_workflow import _iter_node_updates


def test_iter_node_updates_reads_v2_stream_and_ignores_internal_events() -> None:
    chunks = list(
        _iter_node_updates(
            {
                "type": "updates",
                "data": {
                    "classify_email": {"current_node": "classify_email"},
                    "__interrupt__": {"value": "hidden"},
                },
            }
        )
    )

    assert chunks == [
        ("classify_email", {"current_node": "classify_email"}),
    ]


def test_iter_node_updates_ignores_non_update_v2_chunks() -> None:
    assert list(_iter_node_updates({"type": "values", "data": {"status": "done"}})) == []
