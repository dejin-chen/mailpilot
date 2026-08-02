"""本地 JSONL 评测集加载与完整性校验。"""

import json
from pathlib import Path

from pydantic import ValidationError

from evals.schemas import MailEvalCase

DEFAULT_DATASET_PATH = Path(__file__).parent / "datasets" / "mail_agent_cases.jsonl"
DATASET_VERSION = "mail-agent-v1"
MINIMUM_CASE_COUNT = 30


class EvaluationDatasetError(ValueError):
    """评测文件为空、重复或不满足 Schema。"""


def load_cases(path: Path = DEFAULT_DATASET_PATH) -> list[MailEvalCase]:
    """逐行读取 JSONL，并在错误中保留准确行号。"""

    cases: list[MailEvalCase] = []
    seen_ids: set[str] = set()
    for line_number, raw_line in enumerate(
        path.read_text(encoding="utf-8").splitlines(),
        start=1,
    ):
        line = raw_line.strip()
        if not line:
            continue
        try:
            case = MailEvalCase.model_validate_json(line)
        except (ValidationError, ValueError, json.JSONDecodeError) as exc:
            raise EvaluationDatasetError(f"评测集第 {line_number} 行无效：{exc}") from exc
        if case.case_id in seen_ids:
            raise EvaluationDatasetError(f"评测案例 ID 重复：{case.case_id}")
        seen_ids.add(case.case_id)
        cases.append(case)
    if len(cases) < MINIMUM_CASE_COUNT:
        raise EvaluationDatasetError(
            f"评测案例至少需要 {MINIMUM_CASE_COUNT} 条，当前只有 {len(cases)} 条"
        )
    return cases
