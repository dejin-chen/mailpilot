"""从邮件分析到人工审批和写操作执行的完整确定性 LangGraph。"""

from typing import Literal

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.store.base import BaseStore

from app.agent.approval_schemas import (
    ApprovalGateStatus,
    WriteActionProposal,
    WriteExecutionStatus,
)
from app.agent.context import AgentRuntimeContext
from app.agent.nodes.approval import (
    ApprovalRequestCreator,
    PrepareApprovalNode,
    ServiceApprovalRequestCreator,
    WaitForApprovalNode,
)
from app.agent.nodes.classify import ClassifyEmailNode
from app.agent.nodes.execute_tools import ExecuteReadToolsNode, ToolClientFactory
from app.agent.nodes.execute_write import (
    ApprovedActionExecutor,
    ExecuteApprovedActionNode,
)
from app.agent.nodes.extract_intent import ExtractIntentNode
from app.agent.nodes.finalize_workflow import FinalizeWorkflowNode
from app.agent.nodes.load_email import (
    EmailThreadReader,
    LoadEmailNode,
    ServiceEmailThreadReader,
)
from app.agent.nodes.load_memory import LoadMemoryNode
from app.agent.nodes.memory_feedback import (
    ApplyApprovalFeedbackNode,
    MemoryFeedbackUpdater,
    ServiceMemoryFeedbackUpdater,
)
from app.agent.nodes.plan import BuildPlanNode
from app.agent.nodes.prepare_write import (
    BuildCreateEventProposalNode,
    CreateDraftProposalNode,
    GenerateDraftNode,
    RegenerateCreateEventProposalNode,
    SafeToolClientFactory,
)
from app.agent.observability import observe_agent_node
from app.agent.routing import route_after_classification, route_after_node
from app.agent.schemas import (
    AgentRunStatus,
    ExecutionPlan,
    ExtractedIntent,
    ReadOnlyToolName,
)
from app.agent.state import MailAgentInput, MailAgentOutput, MailAgentState
from app.core.config import Settings, get_settings
from app.integrations.llm.client import OpenAICompatibleLlmClient, StructuredLlmClient
from app.integrations.mcp.client import (
    LOCAL_IDEMPOTENT_TOOL_ALLOWLIST,
    READ_ONLY_TOOL_ALLOWLIST,
    MailPilotMcpClient,
)
from app.mcp.schemas import CalendarAvailabilityResult
from app.models.approval import ApprovalAction, ApprovalStatus
from app.observability.base import Observability
from app.observability.factory import get_observability
from app.observability.noop import NoOpObservability

MailProcessingGraph = CompiledStateGraph[
    MailAgentState,
    AgentRuntimeContext,
    MailAgentInput,
    MailAgentOutput,
]


def _route_after_plan(
    state: MailAgentState,
) -> Literal["read_tools", "generate_draft", "finalize", "failed"]:
    if state.get("run_status") is AgentRunStatus.FAILED:
        return "failed"
    plan = state.get("plan")
    if not isinstance(plan, ExecutionPlan):
        return "failed"
    if plan.expected_tool_calls:
        return "read_tools"
    if plan.should_generate_draft:
        return "generate_draft"
    return "finalize"


def _meeting_is_available(state: MailAgentState) -> bool:
    """只相信已成功并通过 MCP Schema 校验的日历可用性结果。"""

    intent = state.get("intent")
    if not isinstance(intent, ExtractedIntent):
        return False
    meeting = intent.meeting
    if not meeting.detected or not meeting.time_information_complete:
        return False
    for result in reversed(state.get("tool_results", [])):
        if (
            result.success
            and result.tool_name is ReadOnlyToolName.CHECK_AVAILABILITY
            and result.output is not None
        ):
            try:
                availability = CalendarAvailabilityResult.model_validate(result.output)
            except Exception:
                return False
            return availability.available
    return False


def _route_after_tools(
    state: MailAgentState,
) -> Literal["calendar_proposal", "generate_draft", "finalize", "failed"]:
    if state.get("run_status") is AgentRunStatus.FAILED:
        return "failed"
    if _meeting_is_available(state):
        return "calendar_proposal"
    plan = state.get("plan")
    if isinstance(plan, ExecutionPlan) and plan.should_generate_draft:
        return "generate_draft"
    return "finalize"


def _route_after_proposal(
    state: MailAgentState,
) -> Literal["approval", "failed"]:
    if state.get("run_status") is AgentRunStatus.FAILED or not isinstance(
        state.get("proposal"), WriteActionProposal
    ):
        return "failed"
    return "approval"


def _route_after_approval(
    state: MailAgentState,
) -> Literal["execute", "feedback", "cancelled"]:
    status = state.get("approval_status")
    if status is ApprovalStatus.APPROVED:
        return "execute"
    if status is ApprovalStatus.FEEDBACK_REQUESTED:
        return "feedback"
    return "cancelled"


def _route_after_feedback(
    state: MailAgentState,
) -> Literal["email", "calendar", "failed"]:
    if state.get("run_status") is AgentRunStatus.FAILED:
        return "failed"
    proposal = state.get("proposal")
    if not isinstance(proposal, WriteActionProposal):
        return "failed"
    if proposal.action is ApprovalAction.SEND_EMAIL:
        return "email"
    if proposal.action is ApprovalAction.CREATE_EVENT:
        return "calendar"
    return "failed"


def _route_after_write(
    state: MailAgentState,
) -> Literal["completed", "failed"]:
    if state.get("execution_status") is WriteExecutionStatus.SUCCEEDED:
        return "completed"
    return "failed"


def build_mail_processing_graph(
    *,
    reader: EmailThreadReader,
    llm_client: StructuredLlmClient,
    read_tool_client_factory: ToolClientFactory,
    safe_tool_client_factory: SafeToolClientFactory,
    approval_creator: ApprovalRequestCreator,
    memory_feedback_updater: MemoryFeedbackUpdater,
    executor: ApprovedActionExecutor,
    checkpointer: BaseCheckpointSaver,
    store: BaseStore,
    observability: Observability | None = None,
) -> MailProcessingGraph:
    """组装有界、可暂停、可恢复的完整邮件处理工作流。"""

    observer = observability or NoOpObservability()
    builder = StateGraph(
        MailAgentState,
        context_schema=AgentRuntimeContext,
        input_schema=MailAgentInput,
        output_schema=MailAgentOutput,
    )
    add_node = lambda name, node: builder.add_node(  # noqa: E731
        name,
        observe_agent_node(name=name, node=node, observability=observer),
    )
    add_node("load_email", LoadEmailNode(reader))
    add_node("load_memory", LoadMemoryNode())
    add_node("classify_email", ClassifyEmailNode(llm_client))
    add_node("extract_intent", ExtractIntentNode(llm_client))
    add_node("build_plan", BuildPlanNode(llm_client))
    add_node(
        "execute_read_tools",
        ExecuteReadToolsNode(read_tool_client_factory),
    )
    add_node("generate_draft", GenerateDraftNode(llm_client))
    add_node(
        "create_draft_proposal",
        CreateDraftProposalNode(safe_tool_client_factory),
    )
    add_node(
        "build_create_event_proposal",
        BuildCreateEventProposalNode(),
    )
    add_node("prepare_approval", PrepareApprovalNode(approval_creator))
    add_node("wait_for_approval", WaitForApprovalNode())
    add_node(
        "apply_approval_feedback",
        ApplyApprovalFeedbackNode(llm_client, memory_feedback_updater),
    )
    add_node(
        "regenerate_create_event_proposal",
        RegenerateCreateEventProposalNode(llm_client),
    )
    add_node(
        "execute_approved_action",
        ExecuteApprovedActionNode(executor),
    )
    add_node("finalize_success", FinalizeWorkflowNode("success"))
    add_node("finalize_ignored", FinalizeWorkflowNode("ignored"))
    add_node("finalize_failed", FinalizeWorkflowNode("failed"))
    add_node("finalize_cancelled", FinalizeWorkflowNode("cancelled"))

    builder.add_edge(START, "load_email")
    builder.add_conditional_edges(
        "load_email",
        route_after_node,
        {"continue": "load_memory", "failed": "finalize_failed"},
    )
    builder.add_conditional_edges(
        "load_memory",
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
        _route_after_plan,
        {
            "read_tools": "execute_read_tools",
            "generate_draft": "generate_draft",
            "finalize": "finalize_success",
            "failed": "finalize_failed",
        },
    )
    builder.add_conditional_edges(
        "execute_read_tools",
        _route_after_tools,
        {
            "calendar_proposal": "build_create_event_proposal",
            "generate_draft": "generate_draft",
            "finalize": "finalize_success",
            "failed": "finalize_failed",
        },
    )
    builder.add_conditional_edges(
        "generate_draft",
        route_after_node,
        {"continue": "create_draft_proposal", "failed": "finalize_failed"},
    )
    builder.add_conditional_edges(
        "create_draft_proposal",
        _route_after_proposal,
        {"approval": "prepare_approval", "failed": "finalize_failed"},
    )
    builder.add_conditional_edges(
        "build_create_event_proposal",
        _route_after_proposal,
        {"approval": "prepare_approval", "failed": "finalize_failed"},
    )
    builder.add_conditional_edges(
        "prepare_approval",
        lambda state: (
            "wait" if state.get("gate_status") is ApprovalGateStatus.WAITING_APPROVAL else "failed"
        ),
        {"wait": "wait_for_approval", "failed": "finalize_failed"},
    )
    builder.add_conditional_edges(
        "wait_for_approval",
        _route_after_approval,
        {
            "execute": "execute_approved_action",
            "feedback": "apply_approval_feedback",
            "cancelled": "finalize_cancelled",
        },
    )
    builder.add_conditional_edges(
        "apply_approval_feedback",
        _route_after_feedback,
        {
            "email": "generate_draft",
            "calendar": "regenerate_create_event_proposal",
            "failed": "finalize_failed",
        },
    )
    builder.add_conditional_edges(
        "regenerate_create_event_proposal",
        _route_after_proposal,
        {"approval": "prepare_approval", "failed": "finalize_failed"},
    )
    builder.add_conditional_edges(
        "execute_approved_action",
        _route_after_write,
        {"completed": "finalize_success", "failed": "finalize_failed"},
    )
    for terminal_node in (
        "finalize_success",
        "finalize_ignored",
        "finalize_failed",
        "finalize_cancelled",
    ):
        builder.add_edge(terminal_node, END)
    return builder.compile(
        checkpointer=checkpointer,
        store=store,
        name="mailpilot_mail_processing_v1",
    )


def build_default_mail_processing_graph(
    *,
    checkpointer: BaseCheckpointSaver,
    store: BaseStore,
    executor: ApprovedActionExecutor,
    settings: Settings | None = None,
) -> MailProcessingGraph:
    """使用正式模型、MCP、Service 和 PostgreSQL Checkpointer 创建完整 Graph。"""

    current_settings = settings or get_settings()
    observer = get_observability()
    llm_client = OpenAICompatibleLlmClient(
        settings=current_settings,
        observability=observer,
    )

    def read_tool_factory(context: AgentRuntimeContext) -> MailPilotMcpClient:
        return MailPilotMcpClient(
            settings=current_settings,
            user_id=context.user_id,
            request_id=context.request_id,
            allowed_tools=READ_ONLY_TOOL_ALLOWLIST,
            observability=observer,
        )

    def safe_tool_factory(context: AgentRuntimeContext) -> MailPilotMcpClient:
        return MailPilotMcpClient(
            settings=current_settings,
            user_id=context.user_id,
            request_id=context.request_id,
            allowed_tools=frozenset({"create_email_draft"}) & LOCAL_IDEMPOTENT_TOOL_ALLOWLIST,
            observability=observer,
        )

    return build_mail_processing_graph(
        reader=ServiceEmailThreadReader(),
        llm_client=llm_client,
        read_tool_client_factory=read_tool_factory,
        safe_tool_client_factory=safe_tool_factory,
        approval_creator=ServiceApprovalRequestCreator(),
        memory_feedback_updater=ServiceMemoryFeedbackUpdater(),
        executor=executor,
        checkpointer=checkpointer,
        store=store,
        observability=observer,
    )
