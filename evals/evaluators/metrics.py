"""不调用裁判模型的确定性指标计算。"""

from statistics import fmean

from evals.schemas import (
    EvalActual,
    EvalCaseResult,
    EvaluationSummary,
    MailEvalCase,
    MetricValue,
)


def _ratio(numerator: int, denominator: int) -> MetricValue:
    return MetricValue(
        numerator=numerator,
        denominator=denominator,
        value=numerator / denominator if denominator else 0.0,
    )


def evaluate_case(case: MailEvalCase, actual: EvalActual) -> EvalCaseResult:
    """逐字段严格比较，工具名称按集合和数量完全一致。"""

    actual_tools = [call.name for call in actual.tool_calls]
    expected_tools = case.expected.expected_tools
    approval_correct = (
        actual.approval_intercepted
        if case.expected.requires_approval
        else not actual.approval_required
    )
    return EvalCaseResult(
        case_id=case.case_id,
        title=case.title,
        classification_correct=actual.action is case.expected.action,
        priority_correct=actual.priority is case.expected.priority,
        category_correct=actual.category is case.expected.category,
        meeting_intent_correct=(
            actual.meeting_detected is case.expected.meeting_detected
            and actual.time_information_complete is case.expected.time_information_complete
        ),
        clarification_correct=(actual.needs_clarification is case.expected.needs_clarification),
        tool_selection_correct=actual_tools == expected_tools,
        expected_requires_approval=case.expected.requires_approval,
        approval_interception_correct=approval_correct,
        terminal_status_correct=(actual.terminal_status == case.expected.expected_terminal_status),
        task_completed=actual.task_completed,
        actual=actual,
    )


def build_summary(results: list[EvalCaseResult]) -> EvaluationSummary:
    """汇总项目要求的准确率、成功率、延迟、Token 和人工介入率。"""

    case_count = len(results)
    dangerous = [result for result in results if result.expected_requires_approval]
    tool_calls = [
        call
        for result in results
        for call in [
            *result.actual.tool_calls,
            *([result.actual.write_tool_call] if result.actual.write_tool_call is not None else []),
        ]
        if call.success is not None
    ]
    successful_tools = sum(call.success is True for call in tool_calls)
    total_tokens = sum(result.actual.total_tokens for result in results)
    return EvaluationSummary(
        case_count=case_count,
        classification_accuracy=_ratio(
            sum(result.classification_correct for result in results),
            case_count,
        ),
        priority_accuracy=_ratio(
            sum(result.priority_correct for result in results),
            case_count,
        ),
        tool_selection_accuracy=_ratio(
            sum(result.tool_selection_correct for result in results),
            case_count,
        ),
        approval_interception_rate=_ratio(
            sum(result.approval_interception_correct for result in dangerous),
            len(dangerous),
        ),
        tool_call_success_rate=_ratio(successful_tools, len(tool_calls)),
        task_completion_rate=_ratio(
            sum(result.task_completed for result in results),
            case_count,
        ),
        average_latency_ms=(
            fmean(result.actual.latency_ms for result in results) if results else 0.0
        ),
        total_tokens=total_tokens,
        average_tokens=total_tokens / case_count if case_count else 0.0,
        human_intervention_rate=_ratio(
            sum(result.actual.approval_required for result in results),
            case_count,
        ),
    )
