"""真实模型运行器使用 Fake Client 的无额度测试。"""

import pytest
from app.agent.schemas import (
    EmailClassification,
    ExecutionPlan,
    ExtractedIntent,
    ModelUsage,
)
from app.integrations.llm.client import StructuredLlmResult

from evals.dataset import load_cases
from evals.runners.model import ModelEvaluationRunner


class FakeModelClient:
    """按目标 Schema 返回固定结构化对象。"""

    def __init__(self) -> None:
        self.operations: list[str] = []

    async def ainvoke_structured(self, *, operation, messages, schema):
        del messages
        self.operations.append(operation)
        if schema is EmailClassification:
            parsed = EmailClassification(
                action="reply",
                priority="high",
                category="request",
                summary="客户要求确认交付日期",
                reason="邮件明确要求回复",
                confidence=0.95,
            )
        elif schema is ExtractedIntent:
            parsed = ExtractedIntent(
                tasks=[],
                meeting={"detected": False},
                needs_clarification=False,
                reason="没有会议意图",
            )
        elif schema is ExecutionPlan:
            parsed = ExecutionPlan(
                goal="生成待审批回复草稿",
                steps=[
                    {
                        "sequence": 1,
                        "action": "generate_draft",
                        "description": "生成回复草稿",
                    }
                ],
                should_generate_draft=True,
            )
        else:
            raise AssertionError(f"未支持的 Schema：{schema}")
        return StructuredLlmResult(
            parsed=parsed,
            usage=ModelUsage(
                operation=operation,
                model_name="fake-model",
                input_tokens=3,
                output_tokens=2,
                total_tokens=5,
                latency_ms=1,
            ),
        )


@pytest.mark.asyncio
async def test_model_runner_only_calls_analysis_components_and_never_mcp() -> None:
    case = next(item for item in load_cases() if item.case_id == "MP-001")
    client = FakeModelClient()

    actual = (await ModelEvaluationRunner(client).run([case]))[0]  # type: ignore[arg-type]

    assert client.operations == [
        "eval_classify_email",
        "eval_extract_intent",
        "eval_build_plan",
    ]
    assert actual.approval_required is True
    assert actual.approval_intercepted is True
    assert actual.terminal_status == "waiting_approval"
    assert actual.total_tokens == 15
    assert actual.tool_calls == []
