"""Langfuse Python SDK v4 的受控适配层。"""

import logging
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from typing import Any
from uuid import UUID

from app.observability.masking import mask_sensitive_data, summarize_value

logger = logging.getLogger(__name__)


class LangfuseObservation:
    """隔离第三方 Observation API，业务层不直接依赖 SDK 类型。"""

    def __init__(self, observation: Any, *, capture_content: bool) -> None:
        self._observation = observation
        self._capture_content = capture_content

    def update(
        self,
        *,
        output: object | None = None,
        metadata: Mapping[str, object] | None = None,
        usage_details: Mapping[str, int] | None = None,
        level: str | None = None,
        status_message: str | None = None,
    ) -> None:
        """把稳定接口转换为 Langfuse v4 update 参数。"""

        payload: dict[str, object] = {}
        if output is not None:
            payload["output"] = (
                mask_sensitive_data(output) if self._capture_content else summarize_value(output)
            )
        if metadata:
            payload["metadata"] = mask_sensitive_data(dict(metadata))
        if usage_details:
            payload["usage_details"] = dict(usage_details)
        if level:
            payload["level"] = level
        if status_message:
            payload["status_message"] = str(mask_sensitive_data(status_message))
        try:
            self._observation.update(**payload)
        except Exception:
            logger.warning("更新 Langfuse Observation 失败", exc_info=True)


class LangfuseObservability:
    """使用确定性 Trace ID 串联一次 AgentRun 的多个恢复请求。"""

    enabled = True

    def __init__(
        self,
        *,
        public_key: str,
        secret_key: str,
        base_url: str | None,
        environment: str,
        release: str,
        capture_content: bool,
        client_factory: Any | None = None,
    ) -> None:
        if client_factory is None:
            from langfuse import Langfuse

            client_factory = Langfuse
        kwargs: dict[str, object] = {
            "public_key": public_key,
            "secret_key": secret_key,
            "environment": environment,
            "release": release,
            "mask": lambda *, data, **_: mask_sensitive_data(data),
        }
        if base_url:
            kwargs["base_url"] = base_url
        self._client = client_factory(**kwargs)
        self.capture_content = capture_content

    @contextmanager
    def trace(
        self,
        *,
        name: str,
        user_id: UUID,
        thread_id: str,
        agent_run_id: UUID,
        request_id: str,
        input: object | None = None,
        metadata: Mapping[str, object] | None = None,
    ) -> Iterator[LangfuseObservation]:
        """创建同一 AgentRun 可重复计算的 Trace，并传播身份属性。"""

        trace_id = self._client.create_trace_id(seed=str(agent_run_id))
        safe_metadata = mask_sensitive_data(
            {
                **dict(metadata or {}),
                "agent_run_id": str(agent_run_id),
                "thread_id": thread_id,
                "request_id": request_id,
            }
        )
        from langfuse import propagate_attributes

        with self._client.start_as_current_observation(
            as_type="span",
            name=name,
            trace_context={"trace_id": trace_id},
            input=self._content_or_summary(input),
            metadata=safe_metadata,
        ) as raw_observation:
            with propagate_attributes(
                trace_name="mailpilot-mail-processing",
                user_id=str(user_id),
                session_id=thread_id,
                metadata={
                    "agent_run_id": str(agent_run_id),
                    "thread_id": thread_id,
                },
                tags=["mailpilot", "mail-processing"],
            ):
                yield LangfuseObservation(
                    raw_observation,
                    capture_content=self.capture_content,
                )

    @contextmanager
    def span(
        self,
        *,
        name: str,
        input: object | None = None,
        metadata: Mapping[str, object] | None = None,
    ) -> Iterator[LangfuseObservation]:
        """在当前 Trace 下创建普通 Span。"""

        with self._client.start_as_current_observation(
            as_type="span",
            name=name,
            input=self._content_or_summary(input),
            metadata=mask_sensitive_data(dict(metadata or {})),
        ) as raw_observation:
            yield LangfuseObservation(
                raw_observation,
                capture_content=self.capture_content,
            )

    @contextmanager
    def generation(
        self,
        *,
        name: str,
        model: str,
        input: object | None = None,
        metadata: Mapping[str, object] | None = None,
    ) -> Iterator[LangfuseObservation]:
        """在当前 Trace 下创建模型 Generation。"""

        with self._client.start_as_current_observation(
            as_type="generation",
            name=name,
            model=model,
            input=self._content_or_summary(input),
            metadata=mask_sensitive_data(dict(metadata or {})),
        ) as raw_observation:
            yield LangfuseObservation(
                raw_observation,
                capture_content=self.capture_content,
            )

    def sanitize(self, value: object) -> object:
        """递归清理发送给 Langfuse 的内容。"""

        return mask_sensitive_data(value)

    def flush(self) -> None:
        """刷新后台批量队列；异常只告警，不影响业务退出。"""

        try:
            self._client.flush()
        except Exception:
            logger.warning("刷新 Langfuse 事件失败", exc_info=True)

    def shutdown(self) -> None:
        """安全关闭 SDK 后台资源。"""

        try:
            self._client.shutdown()
        except Exception:
            logger.warning("关闭 Langfuse 客户端失败", exc_info=True)

    def _content_or_summary(self, value: object | None) -> object | None:
        if value is None:
            return None
        if self.capture_content:
            return mask_sensitive_data(value)
        return summarize_value(value)
