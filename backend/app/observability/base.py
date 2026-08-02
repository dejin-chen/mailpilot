"""业务代码依赖的最小可观测性接口。"""

from collections.abc import Mapping
from contextlib import AbstractContextManager
from typing import Protocol
from uuid import UUID


class Observation(Protocol):
    """一个可更新的 Trace、Span 或模型 Generation。"""

    def update(
        self,
        *,
        output: object | None = None,
        metadata: Mapping[str, object] | None = None,
        usage_details: Mapping[str, int] | None = None,
        level: str | None = None,
        status_message: str | None = None,
    ) -> None:
        """补充执行结果、用量或安全错误摘要。"""


class Observability(Protocol):
    """Langfuse 与空实现都必须满足的稳定接口。"""

    @property
    def enabled(self) -> bool:
        """当前是否真的向外部观察平台发送数据。"""

    @property
    def capture_content(self) -> bool:
        """是否允许记录经过脱敏的业务输入和输出。"""

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
    ) -> AbstractContextManager[Observation]:
        """创建或继续一次 AgentRun 对应的根 Trace。"""

    def span(
        self,
        *,
        name: str,
        input: object | None = None,
        metadata: Mapping[str, object] | None = None,
    ) -> AbstractContextManager[Observation]:
        """创建普通业务或工具步骤。"""

    def generation(
        self,
        *,
        name: str,
        model: str,
        input: object | None = None,
        metadata: Mapping[str, object] | None = None,
    ) -> AbstractContextManager[Observation]:
        """创建专门用于模型调用的 Observation。"""

    def sanitize(self, value: object) -> object:
        """返回可安全发送到观察平台的 JSON 兼容数据。"""

    def flush(self) -> None:
        """尽快发送仍在缓冲区中的观察事件。"""

    def shutdown(self) -> None:
        """释放观察 SDK 的后台资源。"""
