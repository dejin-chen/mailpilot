"""OpenAI Compatible 模型调用集成。"""

from app.integrations.llm.client import (
    OpenAICompatibleLlmClient,
    StructuredLlmClient,
    StructuredLlmResult,
)

__all__ = [
    "OpenAICompatibleLlmClient",
    "StructuredLlmClient",
    "StructuredLlmResult",
]
