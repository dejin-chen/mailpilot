"""Agent State 与节点内部稳定异常。"""


class AgentErrorBase(Exception):
    """MailPilot Agent 内部异常基类。"""


class AgentStateDataError(AgentErrorBase):
    """节点运行所需的 State 字段不存在或不完整。"""

    def __init__(self, field_name: str) -> None:
        super().__init__(f"Agent State 缺少必要字段：{field_name}")
        self.field_name = field_name


class LatestInboundEmailNotFoundError(AgentErrorBase):
    """线程存在，但没有可供 Agent 分析的收件邮件。"""

    def __init__(self) -> None:
        super().__init__("邮件线程中没有收件消息")


class ApprovedActionExecutionError(AgentErrorBase):
    """审批后的写工具失败，并区分已知失败和结果未知。"""

    def __init__(
        self,
        *,
        code: str,
        message: str,
        uncertain: bool = False,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.uncertain = uncertain
