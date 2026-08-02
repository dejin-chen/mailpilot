"""不交给模型生成的 LangGraph 运行时可信上下文。"""

from dataclasses import dataclass
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


@dataclass(frozen=True, slots=True)
class AgentRuntimeContext:
    """由认证后的应用层传入节点，而不是保存在模型业务参数中。"""

    user_id: UUID
    agent_run_id: UUID
    request_id: str
    user_timezone: str = "UTC"

    def __post_init__(self) -> None:
        """拒绝无法用于日志追踪的空请求 ID。"""

        normalized_request_id = self.request_id.strip()
        if not normalized_request_id:
            msg = "AgentRuntimeContext.request_id 不能为空"
            raise ValueError(msg)
        object.__setattr__(self, "request_id", normalized_request_id)
        try:
            ZoneInfo(self.user_timezone)
        except ZoneInfoNotFoundError as exc:
            msg = f"无效用户时区：{self.user_timezone}"
            raise ValueError(msg) from exc
