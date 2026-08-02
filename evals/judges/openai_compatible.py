"""让 DeepEval 使用项目统一的 OpenAI Compatible 模型配置。"""

from typing import Any

from app.core.config import Settings
from app.integrations.llm.exceptions import LlmConfigurationError
from deepeval.models import DeepEvalBaseLLM
from langchain_openai import ChatOpenAI
from pydantic import BaseModel


class OpenAICompatibleJudge(DeepEvalBaseLLM):
    """兼容 DeepSeek、Qwen 等 OpenAI Compatible 裁判端点。"""

    def __init__(self, settings: Settings) -> None:
        if settings.llm_api_key is None:
            raise LlmConfigurationError("LLM_API_KEY")
        if settings.llm_model_name is None:
            raise LlmConfigurationError("LLM_MODEL_NAME")
        self._settings = settings
        super().__init__(model=settings.llm_model_name)

    def load_model(self) -> ChatOpenAI:
        """复用项目超时、重试、模型地址和密钥配置。"""

        settings = self._settings
        return ChatOpenAI(
            model=settings.llm_model_name,
            api_key=settings.llm_api_key,
            base_url=settings.llm_base_url,
            timeout=settings.llm_timeout_seconds,
            max_retries=settings.llm_max_retries,
            temperature=0,
        )

    def generate(
        self,
        prompt: str,
        *,
        schema: type[BaseModel] | None = None,
        **_: Any,
    ) -> str | BaseModel:
        """同步裁判入口；有 Schema 时返回已校验对象。"""

        if schema is not None:
            return self.model.with_structured_output(schema).invoke(prompt)
        response = self.model.invoke(prompt)
        return str(response.content)

    async def a_generate(
        self,
        prompt: str,
        *,
        schema: type[BaseModel] | None = None,
        **_: Any,
    ) -> str | BaseModel:
        """异步裁判入口；DeepEval 可按运行模式选择。"""

        if schema is not None:
            return await self.model.with_structured_output(schema).ainvoke(prompt)
        response = await self.model.ainvoke(prompt)
        return str(response.content)

    def get_model_name(self) -> str:
        """在评测报告中显示实际配置的模型名。"""

        return str(self._settings.llm_model_name)

    def supports_structured_outputs(self) -> bool:
        """告知 DeepEval 可以直接请求 Pydantic 结构化结果。"""

        return True
