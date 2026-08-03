"""聚合多轮真实模型分析链评测，生成稳定的 108 样本统计。"""

from __future__ import annotations

import argparse
import json
from math import ceil
from pathlib import Path
from statistics import fmean, median
from typing import Any

DEFAULT_REPORT_ROOT = Path(__file__).resolve().parents[1] / "reports"


def _ratio(numerator: int, denominator: int) -> dict[str, int | float]:
    return {
        "numerator": numerator,
        "denominator": denominator,
        "value": numerator / denominator if denominator else 0.0,
    }


def _p95(values: list[float]) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    return ordered[max(0, ceil(len(ordered) * 0.95) - 1)]


def _percentage(metric: dict[str, Any]) -> str:
    return (
        f"{metric['value'] * 100:.2f}%"
        f"（{metric['numerator']}/{metric['denominator']}）"
    )


def aggregate(report_paths: list[Path]) -> dict[str, Any]:
    reports = [json.loads(path.read_text(encoding="utf-8")) for path in report_paths]
    cases = [case for report in reports for case in report["cases"]]
    latencies = [float(case["actual"]["latency_ms"]) for case in cases]
    tokens = [int(case["actual"]["total_tokens"]) for case in cases]
    dangerous = [case for case in cases if case["expected_requires_approval"]]
    tool_positive = [case for case in cases if case["expected_tool_count"] > 0]
    errors = [
        {
            "run": run_index,
            "case_id": case["case_id"],
            "error_code": case["actual"]["error_code"],
            "latency_ms": case["actual"]["latency_ms"],
            "tokens": case["actual"]["total_tokens"],
        }
        for run_index, report in enumerate(reports, start=1)
        for case in report["cases"]
        if case["actual"]["error_code"]
    ]
    per_run = []
    for index, report in enumerate(reports, start=1):
        summary = report["summary"]
        per_run.append(
            {
                "run": index,
                "case_count": summary["case_count"],
                "classification_accuracy": summary["classification_accuracy"],
                "tool_selection_accuracy": summary["tool_selection_accuracy"],
                "tool_positive_selection_accuracy": summary[
                    "tool_positive_selection_accuracy"
                ],
                "approval_interception_rate": summary["approval_interception_rate"],
                "task_outcome_accuracy": summary["task_outcome_accuracy"],
                "average_latency_ms": summary["average_latency_ms"],
                "p95_latency_ms": summary["p95_latency_ms"],
                "average_tokens": summary["average_tokens"],
            }
        )
    return {
        "report_name": "MailPilot 真实模型分析链三轮聚合评测",
        "scope": "分类、意图提取、计划生成；不执行 MCP 和危险写操作",
        "run_count": len(reports),
        "sample_count": len(cases),
        "unique_case_count": len({case["case_id"] for case in cases}),
        "classification_accuracy": _ratio(
            sum(case["classification_correct"] for case in cases), len(cases)
        ),
        "priority_accuracy": _ratio(
            sum(case["priority_correct"] for case in cases), len(cases)
        ),
        "tool_selection_accuracy": _ratio(
            sum(case["tool_selection_correct"] for case in cases), len(cases)
        ),
        "tool_positive_selection_accuracy": _ratio(
            sum(case["tool_selection_correct"] for case in tool_positive),
            len(tool_positive),
        ),
        "approval_interception_rate": _ratio(
            sum(case["approval_interception_correct"] for case in dangerous),
            len(dangerous),
        ),
        "analysis_terminal_contract_accuracy": _ratio(
            sum(case["task_outcome_correct"] for case in cases), len(cases)
        ),
        "total_tokens": sum(tokens),
        "average_tokens": fmean(tokens) if tokens else 0.0,
        "median_tokens": median(tokens) if tokens else 0.0,
        "p95_tokens": _p95([float(value) for value in tokens]),
        "average_latency_ms": fmean(latencies) if latencies else 0.0,
        "median_latency_ms": median(latencies) if latencies else 0.0,
        "p95_latency_ms": _p95(latencies),
        "model_error_rate": _ratio(len(errors), len(cases)),
        "errors": errors,
        "per_run": per_run,
        "notes": [
            "三轮使用同一数据集、同一模型和同一参数，顺序执行以降低限流干扰。",
            "分析 Runner 不执行数据集中的审批决定，因此终态契合率不等于任务完成率。",
            "审批拦截指标在本 Runner 中是危险意图判定，不代表 MCP 写操作已实际执行。",
            "本报告只聚合同一组运行；优化结论必须再与同模型、同数据集和同口径的另一组报告对照。",
        ],
    }


def write_report(report: dict[str, Any], output_dir: Path) -> tuple[Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / "model-analysis-3run-aggregate.json"
    markdown_path = output_dir / "model-analysis-3run-aggregate.md"
    json_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    lines = [
        "# MailPilot 真实模型分析链三轮聚合评测",
        "",
        f"- 评测范围：{report['scope']}",
        f"- 数据集：{report['unique_case_count']} 条，重复 {report['run_count']} 轮",
        f"- 总样本：{report['sample_count']}",
        f"- 分类准确率：{_percentage(report['classification_accuracy'])}",
        f"- 优先级准确率：{_percentage(report['priority_accuracy'])}",
        f"- 全量工具选择准确率：{_percentage(report['tool_selection_accuracy'])}",
        (
            "- 需要工具案例选择准确率："
            f"{_percentage(report['tool_positive_selection_accuracy'])}"
        ),
        f"- 危险意图审批拦截率：{_percentage(report['approval_interception_rate'])}",
        (
            "- 分析阶段终态契合率："
            f"{_percentage(report['analysis_terminal_contract_accuracy'])}"
        ),
        f"- 模型错误率：{_percentage(report['model_error_rate'])}",
        f"- 平均 Token：{report['average_tokens']:.2f}",
        f"- P95 Token：{report['p95_tokens']:.2f}",
        f"- 平均延迟：{report['average_latency_ms']:.2f} ms",
        f"- P50 延迟：{report['median_latency_ms']:.2f} ms",
        f"- P95 延迟：{report['p95_latency_ms']:.2f} ms",
        "",
        "## 模型错误",
        "",
        "| 轮次 | 案例 | 错误码 | 延迟（ms） | 已统计 Token |",
        "|---:|---|---|---:|---:|",
    ]
    lines.extend(
        f"| {item['run']} | {item['case_id']} | {item['error_code']} | "
        f"{item['latency_ms']:.2f} | {item['tokens']} |"
        for item in report["errors"]
    )
    lines.extend(["", "## 说明", ""])
    lines.extend(f"- {note}" for note in report["notes"])
    markdown_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return json_path, markdown_path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="聚合 MailPilot 多轮真实模型分析评测")
    parser.add_argument("reports", nargs="+", type=Path)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_REPORT_ROOT)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    json_path, markdown_path = write_report(aggregate(args.reports), args.output_dir)
    print(f"JSON 报告：{json_path}")
    print(f"Markdown 报告：{markdown_path}")


if __name__ == "__main__":
    main()
