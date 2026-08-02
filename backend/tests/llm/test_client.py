"""OpenAI Compatible 模型包装器的无网络测试。"""

from typing import Any

import pytest
from app.agent.schemas import EmailClassification
from app.core.config import Settings
from app.integrations.llm.client import OpenAICompatibleLlmClient
from app.integrations.llm.exceptions import (
    LlmConfigurationError,
    LlmInvocationError,
    LlmStructuredOutputError,
)
from langchain_core.messages import AIMessage, HumanMessage


class FakeStructuredRunnable:
    """返回预设结构化响应或异常。"""

    def __init__(self, response: object) -> None:
        self.response = response
        self.inputs: list[object] = []

    async def ainvoke(self, input: object, **kwargs: Any) -> object:
        del kwargs
        self.inputs.append(input)
        if isinstance(self.response, Exception):
            raise self.response
        return self.response


class SequencedFakeStructuredRunnable:
    """按顺序返回响应，用于验证结构化输出有限重试。"""

    def __init__(self, responses: list[object]) -> None:
        self.responses = responses
        self.inputs: list[object] = []

    async def ainvoke(self, input: object, **kwargs: Any) -> object:
        del kwargs
        self.inputs.append(input)
        index = min(len(self.inputs) - 1, len(self.responses) - 1)
        return self.responses[index]


class FakeChatModel:
    """记录结构化输出配置，不执行真实模型请求。"""

    def __init__(
        self,
        runnable: FakeStructuredRunnable | SequencedFakeStructuredRunnable,
    ) -> None:
        self.runnable = runnable
        self.schema: type[object] | None = None
        self.method: str | None = None
        self.include_raw: bool | None = None

    def with_structured_output(
        self,
        schema: type[object],
        *,
        method: str,
        include_raw: bool,
    ) -> FakeStructuredRunnable:
        self.schema = schema
        self.method = method
        self.include_raw = include_raw
        return self.runnable


def _settings(**changes: object) -> Settings:
    values: dict[str, object] = {
        "_env_file": None,
        "database_url": "postgresql+psycopg://user:password@localhost:5432/mailpilot",
        "redis_url": "redis://localhost:6379/0",
        "jwt_secret_key": "test-jwt-secret-key-at-least-32-characters",
        "llm_api_key": "test-llm-api-key",
        "llm_base_url": "https://llm.example.com/v1",
        "llm_model_name": "test-chat-model",
        "llm_timeout_seconds": 12,
        "llm_max_retries": 1,
        "llm_structured_output_method": "function_calling",
    }
    values.update(changes)
    return Settings(**values)  # type: ignore[arg-type]


def _classification_payload() -> dict[str, object]:
    return {
        "action": "reply",
        "priority": "high",
        "category": "meeting",
        "summary": "需要确认会议",
        "reason": "发件人要求回复",
        "confidence": 0.9,
    }


@pytest.mark.asyncio
async def test_client_returns_validated_schema_and_usage_without_network() -> None:
    raw = AIMessage(
        content="",
        usage_metadata={"input_tokens": 8, "output_tokens": 4, "total_tokens": 12},
    )
    runnable = FakeStructuredRunnable(
        {"raw": raw, "parsed": _classification_payload(), "parsing_error": None}
    )
    fake_model = FakeChatModel(runnable)
    captured_settings: dict[str, object] = {}

    def factory(**kwargs: object) -> FakeChatModel:
        captured_settings.update(kwargs)
        return fake_model

    client = OpenAICompatibleLlmClient(settings=_settings(), model_factory=factory)
    result = await client.ainvoke_structured(
        operation="classify_email",
        messages=[HumanMessage(content="待分析邮件")],
        schema=EmailClassification,
    )

    assert isinstance(result.parsed, EmailClassification)
    assert result.parsed.priority.value == "high"
    assert result.usage.total_tokens == 12
    assert result.usage.operation == "classify_email"
    assert fake_model.schema is EmailClassification
    assert fake_model.method == "function_calling"
    assert fake_model.include_raw is True
    assert captured_settings["base_url"] == "https://llm.example.com/v1"
    assert captured_settings["temperature"] == 0
    assert len(runnable.inputs) == 1


@pytest.mark.parametrize(
    ("changes", "field_name"),
    [
        ({"llm_api_key": None}, "LLM_API_KEY"),
        ({"llm_model_name": None}, "LLM_MODEL_NAME"),
    ],
)
def test_client_rejects_missing_required_model_configuration(
    changes: dict[str, object],
    field_name: str,
) -> None:
    with pytest.raises(LlmConfigurationError) as exc_info:
        OpenAICompatibleLlmClient(settings=_settings(**changes))

    assert exc_info.value.field_name == field_name


@pytest.mark.asyncio
async def test_provider_failure_becomes_stable_invocation_error() -> None:
    fake_model = FakeChatModel(FakeStructuredRunnable(TimeoutError("provider timeout")))
    client = OpenAICompatibleLlmClient(
        settings=_settings(),
        model_factory=lambda **_: fake_model,
    )

    with pytest.raises(LlmInvocationError):
        await client.ainvoke_structured(
            operation="classify_email",
            messages=[HumanMessage(content="邮件正文不应出现在错误信息中")],
            schema=EmailClassification,
        )


@pytest.mark.asyncio
async def test_invalid_structured_response_is_rejected() -> None:
    fake_model = FakeChatModel(
        FakeStructuredRunnable(
            {
                "raw": AIMessage(content=""),
                "parsed": {**_classification_payload(), "priority": "未知级别"},
                "parsing_error": None,
            }
        )
    )
    client = OpenAICompatibleLlmClient(
        settings=_settings(),
        model_factory=lambda **_: fake_model,
    )

    with pytest.raises(LlmStructuredOutputError):
        await client.ainvoke_structured(
            operation="classify_email",
            messages=[HumanMessage(content="待分析邮件")],
            schema=EmailClassification,
        )


@pytest.mark.asyncio
async def test_invalid_structured_response_is_retried_and_usage_is_aggregated() -> None:
    first_raw = AIMessage(
        content="",
        usage_metadata={"input_tokens": 6, "output_tokens": 2, "total_tokens": 8},
    )
    second_raw = AIMessage(
        content="",
        usage_metadata={"input_tokens": 7, "output_tokens": 3, "total_tokens": 10},
    )
    runnable = SequencedFakeStructuredRunnable(
        [
            {"raw": first_raw, "parsed": None, "parsing_error": None},
            {
                "raw": second_raw,
                "parsed": _classification_payload(),
                "parsing_error": None,
            },
        ]
    )
    fake_model = FakeChatModel(runnable)
    client = OpenAICompatibleLlmClient(
        settings=_settings(llm_max_retries=1),
        model_factory=lambda **_: fake_model,
    )

    result = await client.ainvoke_structured(
        operation="classify_email",
        messages=[HumanMessage(content="待分析邮件")],
        schema=EmailClassification,
    )

    assert result.parsed.priority.value == "high"
    assert result.usage.input_tokens == 13
    assert result.usage.output_tokens == 5
    assert result.usage.total_tokens == 18
    assert len(runnable.inputs) == 2
