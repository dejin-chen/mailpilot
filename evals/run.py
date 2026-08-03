"""MailPilot 第 9 阶段统一评测命令。"""

import argparse
import asyncio
import os
from datetime import UTC, datetime
from pathlib import Path

from app.core.config import get_settings
from app.integrations.llm.client import OpenAICompatibleLlmClient
from app.observability.factory import get_observability

from evals.dataset import DATASET_VERSION, DEFAULT_DATASET_PATH, load_cases
from evals.evaluators.deepeval_metrics import (
    evaluate_draft_quality_with_deepeval,
    evaluate_tool_correctness_with_deepeval,
)
from evals.evaluators.metrics import build_summary, evaluate_case
from evals.judges.openai_compatible import OpenAICompatibleJudge
from evals.reporting import write_report
from evals.runners.model import ModelEvaluationRunner
from evals.runners.reference import ReferenceEvaluationRunner
from evals.schemas import EvaluationReport


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="运行 MailPilot 离线评测")
    parser.add_argument(
        "--mode",
        choices=("reference", "model"),
        default="reference",
        help="reference 不调用模型；model 使用真实 OpenAI Compatible 模型",
    )
    parser.add_argument(
        "--dataset",
        type=Path,
        default=DEFAULT_DATASET_PATH,
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path(__file__).parent / "reports",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="只限制真实模型评测数量，参考基线始终运行完整数据集",
    )
    parser.add_argument(
        "--judge-drafts",
        action="store_true",
        help="额外调用裁判模型评价带草稿的案例",
    )
    parser.add_argument(
        "--analysis-mode",
        choices=("sequential", "optimized"),
        default="optimized",
        help="model 模式使用的分析链路；optimized 减少一次模型往返",
    )
    return parser.parse_args()


async def async_main(args: argparse.Namespace) -> tuple[Path, Path]:
    """运行评测、计算指标并生成双格式报告。"""

    os.environ.setdefault("DEEPEVAL_TELEMETRY_OPT_OUT", "1")
    cases = load_cases(args.dataset)
    if args.mode == "reference":
        actuals = await ReferenceEvaluationRunner().run(cases)
        notes = [
            "reference 是 Fake 依赖驱动的参考契约基线，不代表真实模型线上准确率。",
            "普通 pytest 和该模式均不会调用外部模型或执行真实写操作。",
        ]
    else:
        if args.limit is not None:
            if args.limit < 1:
                raise ValueError("--limit 必须大于 0")
            cases = cases[: args.limit]
        settings = get_settings()
        actuals = await ModelEvaluationRunner(
            OpenAICompatibleLlmClient(
                settings=settings,
                observability=get_observability(),
            ),
            analysis_mode=args.analysis_mode,
        ).run(cases)
        notes = [
            "model 模式只运行分类、意图和计划组件，不调用 MCP 或危险写工具。",
            f"分析链路模式：{args.analysis_mode}。",
            "模型成绩受提供商和模型版本影响，不应写成未经限定的生产指标。",
        ]

    actuals_by_id = {actual.case_id: actual for actual in actuals}
    case_results = [evaluate_case(case, actuals_by_id[case.case_id]) for case in cases]
    supplemental = {
        "deepeval_tool_correctness": evaluate_tool_correctness_with_deepeval(
            cases,
            case_results,
        )
    }
    if args.judge_drafts:
        supplemental["deepeval_draft_quality"] = evaluate_draft_quality_with_deepeval(
            cases,
            case_results,
            judge=OpenAICompatibleJudge(get_settings()),
        )
    report = EvaluationReport(
        report_name="MailPilot 邮件 Agent 离线评测",
        mode=args.mode,
        dataset_version=DATASET_VERSION,
        generated_at=datetime.now(UTC),
        summary=build_summary(case_results),
        cases=case_results,
        supplemental_metrics=supplemental,
        notes=notes,
    )
    return write_report(report, args.output_dir)


def main() -> None:
    """命令行入口。"""

    json_path, markdown_path = asyncio.run(async_main(parse_args()))
    print(f"JSON 报告：{json_path}")
    print(f"Markdown 报告：{markdown_path}")


if __name__ == "__main__":
    main()
