"""不发送外部数据的可观测性空实现。"""

from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from uuid import UUID

from app.observability.masking import mask_sensitive_data


class NoOpObservation:
    """接受更新但不执行任何 I/O。"""

    def update(
        self,
        *,
        output: object | None = None,
        metadata: Mapping[str, object] | None = None,
        usage_details: Mapping[str, int] | None = None,
        level: str | None = None,
        status_message: str | None = None,
    ) -> None:
        """故意忽略所有观察数据。"""


class NoOpObservability:
    """Langfuse 未启用或配置不完整时使用。"""

    enabled = False

    def __init__(self, *, capture_content: bool = False) -> None:
        self.capture_content = capture_content
        self._observation = NoOpObservation()

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
    ) -> Iterator[NoOpObservation]:
        """返回可安全使用的空根 Trace。"""

        yield self._observation

    @contextmanager
    def span(
        self,
        *,
        name: str,
        input: object | None = None,
        metadata: Mapping[str, object] | None = None,
    ) -> Iterator[NoOpObservation]:
        """返回空 Span。"""

        yield self._observation

    @contextmanager
    def generation(
        self,
        *,
        name: str,
        model: str,
        input: object | None = None,
        metadata: Mapping[str, object] | None = None,
    ) -> Iterator[NoOpObservation]:
        """返回空 Generation。"""

        yield self._observation

    def sanitize(self, value: object) -> object:
        """仍提供与正式实现一致的脱敏行为，方便测试。"""

        return mask_sensitive_data(value)

    def flush(self) -> None:
        """空实现无需刷新。"""

    def shutdown(self) -> None:
        """空实现没有后台资源。"""
