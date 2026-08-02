"""MailPilot 第一个可执行的确定性 LangGraph 核心工作流。"""

from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from app.agent.context import AgentRuntimeContext
from app.agent.nodes.classify import ClassifyEmailNode
from app.agent.nodes.execute_tools import ExecuteReadToolsNode, ToolClientFactory
from app.agent.nodes.extract_intent import ExtractIntentNode
from app.agent.nodes.finalize import FinalizeNode
from app.agent.nodes.load_email import EmailThreadReader, LoadEmailNode, ServiceEmailThreadReader
from app.agent.nodes.plan import BuildPlanNode
from app.agent.routing import (
    route_after_classification,
    route_after_node,
    route_after_plan,
    route_after_tools,
)
from app.agent.state import MailAgentOutput, MailAgentState
from app.core.config import Settings, get_settings
from app.integrations.llm.client import OpenAICompatibleLlmClient, StructuredLlmClient
from app.integrations.mcp.client import READ_ONLY_TOOL_ALLOWLIST, MailPilotMcpClient

MailAgentGraph = CompiledStateGraph[
    MailAgentState,
    AgentRuntimeContext,
    MailAgentState,
    MailAgentOutput,
]


def build_mail_agent_graph(
    *,
    reader: EmailThreadReader,
    llm_client: StructuredLlmClient,
    tool_client_factory: ToolClientFactory,
) -> MailAgentGraph:
    """使用可注入依赖组装 Graph，生产与测试共享完全相同的路线。"""

    builder = StateGraph(
        MailAgentState,
        context_schema=AgentRuntimeContext,
        output_schema=MailAgentOutput,
    )
    builder.add_node("load_email", LoadEmailNode(reader))
    builder.add_node("classify_email", ClassifyEmailNode(llm_client))
    builder.add_node("extract_intent", ExtractIntentNode(llm_client))
    builder.add_node("build_plan", BuildPlanNode(llm_client))
    builder.add_node("execute_read_tools", ExecuteReadToolsNode(tool_client_factory))
    builder.add_node("finalize_success", FinalizeNode("success"))
    builder.add_node("finalize_ignored", FinalizeNode("ignored"))
    builder.add_node("finalize_failed", FinalizeNode("failed"))

    builder.add_edge(START, "load_email")
    builder.add_conditional_edges(
        "load_email",
        route_after_node,
        {"continue": "classify_email", "failed": "finalize_failed"},
    )
    builder.add_conditional_edges(
        "classify_email",
        route_after_classification,
        {
            "continue": "extract_intent",
            "ignored": "finalize_ignored",
            "failed": "finalize_failed",
        },
    )
    builder.add_conditional_edges(
        "extract_intent",
        route_after_node,
        {"continue": "build_plan", "failed": "finalize_failed"},
    )
    builder.add_conditional_edges(
        "build_plan",
        route_after_plan,
        {
            "execute_tools": "execute_read_tools",
            "finalize": "finalize_success",
            "failed": "finalize_failed",
        },
    )
    builder.add_conditional_edges(
        "execute_read_tools",
        route_after_tools,
        {"finalize": "finalize_success", "failed": "finalize_failed"},
    )
    builder.add_edge("finalize_success", END)
    builder.add_edge("finalize_ignored", END)
    builder.add_edge("finalize_failed", END)
    return builder.compile(name="mailpilot_core_workflow")


def build_default_mail_agent_graph(settings: Settings | None = None) -> MailAgentGraph:
    """使用正式 Service、模型 Client 和只读 MCP Client 创建生产 Graph。"""

    current_settings = settings or get_settings()

    def tool_client_factory(context: AgentRuntimeContext) -> MailPilotMcpClient:
        return MailPilotMcpClient(
            settings=current_settings,
            user_id=context.user_id,
            request_id=context.request_id,
            allowed_tools=READ_ONLY_TOOL_ALLOWLIST,
        )

    return build_mail_agent_graph(
        reader=ServiceEmailThreadReader(),
        llm_client=OpenAICompatibleLlmClient(settings=current_settings),
        tool_client_factory=tool_client_factory,
    )
