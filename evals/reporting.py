"""把评测结果保存为机器 JSON 和中文 Markdown。"""

import json
from pathlib import Path

from evals.schemas import EvaluationReport, MetricValue


def _percentage(metric: MetricValue) -> str:
    return f"{metric.value * 100:.2f}%（{metric.numerator:g}/{metric.denominator}）"


def write_report(report: EvaluationReport, output_dir: Path) -> tuple[Path, Path]:
    """原子需求不高的本地评测产物写入；目录不存在时创建。"""

    output_dir.mkdir(parents=True, exist_ok=True)
    stem = f"{report.dataset_version}-{report.mode}"
    json_path = output_dir / f"{stem}.json"
    markdown_path = output_dir / f"{stem}.md"
    json_path.write_text(
        json.dumps(report.model_dump(mode="json"), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    summary = report.summary
    lines = [
        f"# MailPilot 评测报告：{report.mode}",
        "",
        f"- 数据集版本：`{report.dataset_version}`",
        f"- 案例数量：{summary.case_count}",
        f"- 生成时间：{report.generated_at.isoformat()}",
        "",
        "## 核心指标",
        "",
        f"- 邮件分类准确率：{_percentage(summary.classification_accuracy)}",
        f"- 优先级准确率：{_percentage(summary.priority_accuracy)}",
        f"- 工具选择准确率：{_percentage(summary.tool_selection_accuracy)}",
        (
            "- 需要工具案例的选择准确率："
            f"{_percentage(summary.tool_positive_selection_accuracy)}"
        ),
        f"- 必须审批操作拦截率：{_percentage(summary.approval_interception_rate)}",
        f"- 工具调用成功率：{_percentage(summary.tool_call_success_rate)}",
        f"- 原始任务完成率：{_percentage(summary.task_completion_rate)}",
        f"- 任务结果符合预期率：{_percentage(summary.task_outcome_accuracy)}",
        f"- 平均延迟：{summary.average_latency_ms:.2f} ms",
        f"- P50 延迟：{summary.median_latency_ms:.2f} ms",
        f"- P95 延迟：{summary.p95_latency_ms:.2f} ms",
        f"- 总 Token：{summary.total_tokens}",
        f"- 平均 Token：{summary.average_tokens:.2f}",
        f"- 人工介入率：{_percentage(summary.human_intervention_rate)}",
    ]
    if report.supplemental_metrics:
        lines.extend(["", "## 补充指标", ""])
        lines.extend(
            f"- {name}：{value:.4f}" for name, value in sorted(report.supplemental_metrics.items())
        )
    if report.notes:
        lines.extend(["", "## 说明", ""])
        lines.extend(f"- {note}" for note in report.notes)
    markdown_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return json_path, markdown_path
