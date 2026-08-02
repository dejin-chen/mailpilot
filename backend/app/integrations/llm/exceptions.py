"""模型配置、调用与结构化输出异常。"""


class LlmError(Exception):
    """MailPilot 模型层异常基类。"""


class LlmConfigurationError(LlmError):
    """缺少模型调用所需配置。"""

    def __init__(self, field_name: str) -> None:
        super().__init__(f"模型配置缺失：{field_name}")
        self.field_name = field_name


class LlmInvocationError(LlmError):
    """模型网络请求或提供商调用失败。"""

    def __init__(self, operation: str) -> None:
        super().__init__(f"模型调用失败：{operation}")
        self.operation = operation


class LlmStructuredOutputError(LlmError):
    """模型返回内容无法通过目标 Pydantic Schema 校验。"""

    def __init__(self, operation: str) -> None:
        super().__init__(f"模型结构化输出校验失败：{operation}")
        self.operation = operation
