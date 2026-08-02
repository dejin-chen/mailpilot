"""指标公式与危险操作分母测试。"""

from evals.dataset import load_cases
from evals.evaluators.metrics import build_summary, evaluate_case
from evals.schemas import EvalActual


def test_missing_approval_is_counted_as_failed_interception() -> None:
    case = next(item for item in load_cases() if item.case_id == "MP-022")
    actual = EvalActual(
        case_id=case.case_id,
        action=case.expected.action,
        priority=case.expected.priority,
        category=case.expected.category,
        meeting_detected=False,
        time_information_complete=False,
        needs_clarification=False,
        approval_required=False,
        approval_intercepted=False,
        terminal_status="completed",
        task_completed=True,
        latency_ms=2,
    )

    summary = build_summary([evaluate_case(case, actual)])

    assert summary.approval_interception_rate.denominator == 1
    assert summary.approval_interception_rate.value == 0
    assert summary.human_intervention_rate.value == 0


def test_tool_selection_requires_exact_ordered_list() -> None:
    case = next(item for item in load_cases() if item.case_id == "MP-010")
    actual = EvalActual(
        case_id=case.case_id,
        action=case.expected.action,
        priority=case.expected.priority,
        category=case.expected.category,
        meeting_detected=True,
        time_information_complete=True,
        needs_clarification=False,
        tool_calls=[],
        approval_required=True,
        approval_intercepted=True,
        terminal_status="waiting_approval",
        task_completed=True,
        latency_ms=2,
    )

    result = evaluate_case(case, actual)

    assert result.tool_selection_correct is False
