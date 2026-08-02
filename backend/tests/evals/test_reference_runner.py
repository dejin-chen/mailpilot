"""无模型参考基线和 DeepEval 确定性适配测试。"""

import pytest

from evals.dataset import load_cases
from evals.evaluators.deepeval_metrics import (
    evaluate_tool_correctness_with_deepeval,
)
from evals.evaluators.metrics import build_summary, evaluate_case
from evals.runners.reference import ReferenceEvaluationRunner


@pytest.mark.asyncio
async def test_reference_runner_produces_required_metrics_without_tokens() -> None:
    cases = load_cases()
    actuals = await ReferenceEvaluationRunner().run(cases)
    by_id = {actual.case_id: actual for actual in actuals}
    results = [evaluate_case(case, by_id[case.case_id]) for case in cases]
    summary = build_summary(results)

    assert summary.case_count == 36
    assert summary.classification_accuracy.value == 1
    assert summary.tool_selection_accuracy.value == 1
    assert summary.approval_interception_rate.value == 1
    assert summary.tool_call_success_rate.denominator > 0
    assert summary.tool_call_success_rate.value < 1
    assert summary.total_tokens == 0
    assert evaluate_tool_correctness_with_deepeval(cases, results) == 1
