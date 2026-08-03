"""HTTP 端到端评测结果映射测试。"""

from evals.benchmarks.e2e_http import E2eHttpRunner
from evals.dataset import load_cases


def test_ignored_graph_status_is_not_misreported_as_completed() -> None:
    """数据库 completed 表示流程正常结束，忽略业务终态要读取 Graph 结果。"""

    case = next(item for item in load_cases() if item.case_id == "MP-003")
    actual = E2eHttpRunner._to_actual(
        case,
        {
            "status": "completed",
            "result": {
                "run_status": "ignored",
                "classification": {
                    "action": "ignore",
                    "priority": "low",
                    "category": "newsletter",
                },
                "tool_results": [],
            },
            "input_tokens": 10,
            "output_tokens": 5,
            "total_tokens": 15,
            "error_code": None,
        },
        approval_seen=False,
        approval_id=None,
        latency_ms=10,
    )

    assert actual.terminal_status == "ignored"
    assert actual.task_completed is True


def test_waiting_approval_requires_persisted_analysis_snapshot() -> None:
    incomplete = {
        "status": "waiting_approval",
        "result": {},
        "total_tokens": 0,
    }
    complete = {
        "status": "waiting_approval",
        "result": {
            "classification": {},
            "intent": {},
            "plan": {},
        },
        "total_tokens": 123,
    }

    assert E2eHttpRunner._snapshot_is_materialized(incomplete) is False
    assert E2eHttpRunner._snapshot_is_materialized(complete) is True
