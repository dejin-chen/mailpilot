"""DeepEval 适配：确定性工具匹配与可选草稿质量裁判。"""

from statistics import fmean

from deepeval.metrics import GEval, ToolCorrectnessMetric
from deepeval.models import DeepEvalBaseLLM
from deepeval.test_case import LLMTestCase, SingleTurnParams, ToolCall

from evals.schemas import EvalCaseResult, MailEvalCase


class _NoNetworkJudge(DeepEvalBaseLLM):
    """满足 DeepEval 初始化要求；工具精确匹配不应真正调用它。"""

    def load_model(self) -> "_NoNetworkJudge":
        return self

    def generate(self, *args: object, **kwargs: object) -> str:
        raise RuntimeError("确定性工具评测不允许调用裁判模型")

    async def a_generate(self, *args: object, **kwargs: object) -> str:
        raise RuntimeError("确定性工具评测不允许调用裁判模型")

    def get_model_name(self) -> str:
        return "mailpilot-no-network-judge"


def evaluate_tool_correctness_with_deepeval(
    cases: list[MailEvalCase],
    results: list[EvalCaseResult],
) -> float:
    """不传 available_tools，避免该指标额外调用裁判模型。"""

    cases_by_id = {case.case_id: case for case in cases}
    scores: list[float] = []
    for result in results:
        case = cases_by_id[result.case_id]
        metric = ToolCorrectnessMetric(
            async_mode=False,
            include_reason=False,
            should_exact_match=True,
            model=_NoNetworkJudge(),
        )
        test_case = LLMTestCase(
            name=case.case_id,
            input=f"{case.input.subject}\n{case.input.body}",
            actual_output=result.actual.terminal_status,
            tools_called=[ToolCall(name=call.name) for call in result.actual.tool_calls],
            expected_tools=[ToolCall(name=name) for name in case.expected.expected_tools],
        )
        metric.measure(test_case)
        scores.append(float(metric.score or 0))
    return fmean(scores) if scores else 0.0


def evaluate_draft_quality_with_deepeval(
    cases: list[MailEvalCase],
    results: list[EvalCaseResult],
    *,
    judge: DeepEvalBaseLLM,
) -> float:
    """只评价明确提供草稿与期望的案例，不能替代确定性安全测试。"""

    cases_by_id = {case.case_id: case for case in cases}
    scores: list[float] = []
    for result in results:
        case = cases_by_id[result.case_id]
        if result.actual.draft_text is None or case.expected.draft_expectation is None:
            continue
        metric = GEval(
            name="企业邮件草稿质量",
            criteria=("判断草稿是否准确回应邮件、没有虚构已完成的操作、语气专业，并满足期望说明。"),
            evaluation_params=[
                SingleTurnParams.INPUT,
                SingleTurnParams.ACTUAL_OUTPUT,
                SingleTurnParams.EXPECTED_OUTPUT,
            ],
            model=judge,
            threshold=0.7,
            async_mode=False,
        )
        metric.measure(
            LLMTestCase(
                name=case.case_id,
                input=f"{case.input.subject}\n{case.input.body}",
                actual_output=result.actual.draft_text,
                expected_output=case.expected.draft_expectation,
            )
        )
        scores.append(float(metric.score or 0))
    if not scores:
        raise ValueError("没有同时包含 draft_text 和 draft_expectation 的可评价案例")
    return fmean(scores)
