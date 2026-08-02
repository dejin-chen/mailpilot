"""FastAPI 获取应用级 LangGraph Checkpointer 的依赖。"""

from typing import Annotated

from fastapi import Depends, Request
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.store.base import BaseStore

from app.agent.nodes.execute_write import (
    ApprovedActionExecutor,
    ServiceApprovedActionExecutor,
)
from app.agent.workflow import build_default_mail_processing_graph
from app.services.exceptions import (
    AgentCheckpointerUnavailableError,
    AgentMemoryStoreUnavailableError,
)
from app.services.mail_processing_workflow import MailProcessingGraphFactory

AgentCheckpointer = Annotated[BaseCheckpointSaver, "应用级 LangGraph Checkpointer"]
AgentStore = Annotated[BaseStore, "应用级 LangGraph 长期记忆 Store"]
WriteActionExecutor = Annotated[ApprovedActionExecutor, "审批后写工具执行器"]
ProcessingGraphFactory = Annotated[
    MailProcessingGraphFactory,
    "完整邮件处理 Graph 延迟工厂",
]


def get_agent_checkpointer(request: Request) -> AgentCheckpointer:
    """读取 lifespan 打开的 Checkpointer；未启动时返回明确服务错误。"""

    checkpointer = getattr(request.app.state, "agent_checkpointer", None)
    if checkpointer is None:
        raise AgentCheckpointerUnavailableError
    return checkpointer


def get_agent_store(request: Request) -> AgentStore:
    """读取 lifespan 打开的长期 Store；未启动时返回明确服务错误。"""

    store = getattr(request.app.state, "agent_store", None)
    if store is None:
        raise AgentMemoryStoreUnavailableError
    return store


def get_approved_action_executor(request: Request) -> WriteActionExecutor:
    """测试可从 app.state 注入 Fake，正式环境使用 MCP 写工具执行器。"""

    executor = getattr(request.app.state, "approved_action_executor", None)
    if executor is not None:
        return executor
    return ServiceApprovedActionExecutor()


def get_mail_processing_graph_factory(
    request: Request,
    checkpointer: Annotated[BaseCheckpointSaver, Depends(get_agent_checkpointer)],
    executor: Annotated[ApprovedActionExecutor, Depends(get_approved_action_executor)],
) -> ProcessingGraphFactory:
    """测试可注入 Fake Graph 工厂；正式环境延迟组装完整工作流。"""

    factory = getattr(request.app.state, "mail_processing_graph_factory", None)
    if factory is not None:
        return factory

    def build_graph():
        # 旧 approval_gate_v1 恢复时不会调用本工厂，因此 Store 检查必须延迟到
        # 真正构建完整邮件 Graph 的时刻，避免破坏已暂停的旧工作流。
        store = getattr(request.app.state, "agent_store", None)
        if store is None:
            raise AgentMemoryStoreUnavailableError
        return build_default_mail_processing_graph(
            checkpointer=checkpointer,
            store=store,
            executor=executor,
        )

    return build_graph
