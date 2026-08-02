"""带结构化输出、Token 统计和安全日志的模型调用边界。"""

import logging
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from time import perf_counter
from typing import Any, Protocol, cast

from langchain_core.messages import BaseMessage
from langchain_openai import ChatOpenAI
from pydantic import BaseModel, ValidationError

from app.agent.schemas import ModelUsage
from app.core.config import Settings
from app.integrations.llm.exceptions import (
    LlmConfigurationError,
    LlmInvocationError,
    LlmStructuredOutputError,
)
from app.observability.base import Observability
from app.observability.noop import NoOpObservability

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class StructuredLlmResult[SchemaT: BaseModel]:
    """一次通过 Pydantic 校验的模型结果及其用量。"""

    parsed: SchemaT
    usage: ModelUsage


class StructuredLlmClient(Protocol):
    """Agent 节点依赖的最小模型接口，测试可注入 Fake 实现。"""

    async def ainvoke_structured[SchemaT: BaseModel](
        self,
        *,
        operation: str,
        messages: Sequence[BaseMessage],
        schema: type[SchemaT],
    ) -> StructuredLlmResult[SchemaT]: ...


class StructuredRunnable(Protocol):
    """`with_structured_output` 返回对象的最小异步接口。"""

    async def ainvoke(self, input: object, **kwargs: Any) -> Any: ...


class StructuredChatModel(Protocol):
    """生产 ChatOpenAI 与测试 Fake 共用的最小接口。"""

    def with_structured_output(
        self,
        schema: type[BaseModel],
        *,
        method: str,
        include_raw: bool,
    ) -> StructuredRunnable: ...


ChatModelFactory = Callable[..., StructuredChatModel]


class OpenAICompatibleLlmClient:
    """包装 ChatOpenAI，不向节点暴露提供商细节和原始异常。"""

    def __init__(
        self,
        *,
        settings: Settings,
        model_factory: ChatModelFactory | None = None,
        observability: Observability | None = None,
    ) -> None:
        if settings.llm_api_key is None:
            raise LlmConfigurationError("LLM_API_KEY")
        if settings.llm_model_name is None:
            raise LlmConfigurationError("LLM_MODEL_NAME")

        self._model_name = settings.llm_model_name
        self._structured_output_method = settings.llm_structured_output_method
        self._structured_output_retries = settings.llm_max_retries
        self._observability = observability or NoOpObservability()
        factory = model_factory or cast(ChatModelFactory, ChatOpenAI)
        self._model = factory(
            model=settings.llm_model_name,
            api_key=settings.llm_api_key,
            base_url=settings.llm_base_url,
            timeout=settings.llm_timeout_seconds,
            max_retries=settings.llm_max_retries,
            temperature=0,
        )

    async def ainvoke_structured[SchemaT: BaseModel](
        self,
        *,
        operation: str,
        messages: Sequence[BaseMessage],
        schema: type[SchemaT],
    ) -> StructuredLlmResult[SchemaT]:
        """调用模型并把成功结果统一转换成目标 Pydantic 类型。"""

        normalized_operation = operation.strip()
        if not normalized_operation or not messages:
            raise LlmInvocationError(operation or "unknown")

        started_at = perf_counter()
        with self._observability.generation(
            name=normalized_operation,
            model=self._model_name,
            input=self._message_payload(messages),
            metadata={
                "schema": schema.__name__,
                "structured_output_method": self._structured_output_method,
            },
        ) as generation:
            raw_responses: list[object | None] = []
            max_attempts = self._structured_output_retries + 1
            parsed: SchemaT | None = None
            last_error: LlmStructuredOutputError | None = None
            for attempt in range(1, max_attempts + 1):
                try:
                    runnable = self._model.with_structured_output(
                        schema,
                        method=self._structured_output_method,
                        include_raw=True,
                    )
                    response = await runnable.ainvoke(list(messages))
                except Exception as exc:
                    generation.update(
                        level="ERROR",
                        status_message="模型调用失败",
                        metadata={
                            "error_type": type(exc).__name__,
                            "attempt": attempt,
                        },
                    )
                    logger.warning(
                        "模型调用失败",
                        extra={
                            "operation": normalized_operation,
                            "model_name": self._model_name,
                            "error_type": type(exc).__name__,
                            "attempt": attempt,
                        },
                    )
                    raise LlmInvocationError(normalized_operation) from exc

                raw_responses.append(
                    response.get("raw") if isinstance(response, dict) else None
                )
                try:
                    parsed, _ = self._parse_response(
                        operation=normalized_operation,
                        response=response,
                        schema=schema,
                    )
                    break
                except LlmStructuredOutputError as exc:
                    last_error = exc
                    logger.warning(
                        "模型结构化输出校验失败",
                        extra={
                            "operation": normalized_operation,
                            "model_name": self._model_name,
                            "attempt": attempt,
                            "max_attempts": max_attempts,
                        },
                    )

            if parsed is None:
                generation.update(
                    level="ERROR",
                    status_message="模型结构化输出校验失败",
                    metadata={
                        "schema": schema.__name__,
                        "attempts": max_attempts,
                    },
                )
                raise last_error or LlmStructuredOutputError(normalized_operation)

            latency_ms = (perf_counter() - started_at) * 1000
            usage = self._build_aggregate_usage(
                operation=normalized_operation,
                raw_responses=raw_responses,
                latency_ms=latency_ms,
            )
            generation.update(
                output=parsed,
                usage_details={
                    "input": usage.input_tokens,
                    "output": usage.output_tokens,
                    "total": usage.total_tokens,
                },
                metadata={
                    "latency_ms": round(usage.latency_ms, 3),
                    "attempts": len(raw_responses),
                },
            )
            return StructuredLlmResult(parsed=parsed, usage=usage)

    def _build_aggregate_usage(
        self,
        *,
        operation: str,
        raw_responses: Sequence[object | None],
        latency_ms: float,
    ) -> ModelUsage:
        """合并结构化重试产生的 Token，避免只统计最后一次成功调用。"""

        usages = [
            self._build_usage(operation=operation, raw=raw, latency_ms=0)
            for raw in raw_responses
        ]
        return ModelUsage(
            operation=operation,
            model_name=self._model_name,
            input_tokens=sum(item.input_tokens for item in usages),
            output_tokens=sum(item.output_tokens for item in usages),
            total_tokens=sum(item.total_tokens for item in usages),
            latency_ms=latency_ms,
        )

    @staticmethod
    def _parse_response[SchemaT: BaseModel](
        *,
        operation: str,
        response: object,
        schema: type[SchemaT],
    ) -> tuple[SchemaT, object | None]:
        """处理 include_raw 响应，并拒绝解析错误或错误类型。"""

        if not isinstance(response, dict):
            raise LlmStructuredOutputError(operation)
        if response.get("parsing_error") is not None:
            raise LlmStructuredOutputError(operation)

        parsed = response.get("parsed")
        try:
            validated = parsed if isinstance(parsed, schema) else schema.model_validate(parsed)
        except (TypeError, ValidationError) as exc:
            raise LlmStructuredOutputError(operation) from exc
        return validated, response.get("raw")

    def _build_usage(
        self,
        *,
        operation: str,
        raw: object | None,
        latency_ms: float,
    ) -> ModelUsage:
        """兼容缺失 Token 统计的 OpenAI Compatible 提供商。"""

        metadata = getattr(raw, "usage_metadata", None)
        usage = metadata if isinstance(metadata, dict) else {}
        input_tokens = self._non_negative_int(usage.get("input_tokens"))
        output_tokens = self._non_negative_int(usage.get("output_tokens"))
        reported_total = self._non_negative_int(usage.get("total_tokens"))
        total_tokens = max(reported_total, input_tokens + output_tokens)
        return ModelUsage(
            operation=operation,
            model_name=self._model_name,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            total_tokens=total_tokens,
            latency_ms=latency_ms,
        )

    @staticmethod
    def _non_negative_int(value: object) -> int:
        """忽略第三方兼容接口中的无效 Token 字段。"""

        return value if isinstance(value, int) and value >= 0 else 0

    def _message_payload(self, messages: Sequence[BaseMessage]) -> object:
        """默认只暴露消息类型；显式允许时才交给统一脱敏层处理内容。"""

        if not self._observability.capture_content:
            return {
                "message_count": len(messages),
                "message_types": [message.type for message in messages],
            }
        return [
            {
                "type": message.type,
                "content": message.content,
            }
            for message in messages
        ]
