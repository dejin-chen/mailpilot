"""Agent 节点之间传递的结构化业务结果。"""

from datetime import datetime
from enum import StrEnum
from typing import Self
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import (
    BaseModel,
    ConfigDict,
    EmailStr,
    Field,
    JsonValue,
    field_validator,
    model_validator,
)


class AgentSchema(BaseModel):
    """所有模型结构化输出的严格基类。"""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class EmailAction(StrEnum):
    """Agent 对邮件的总体处理判断。"""

    REPLY = "reply"
    REMIND = "remind"
    IGNORE = "ignore"


class EmailPriority(StrEnum):
    """邮件处理优先级。"""

    LOW = "low"
    NORMAL = "normal"
    HIGH = "high"
    URGENT = "urgent"


class EmailCategory(StrEnum):
    """第一版邮件业务分类。"""

    MEETING = "meeting"
    TASK = "task"
    REQUEST = "request"
    NOTIFICATION = "notification"
    NEWSLETTER = "newsletter"
    OTHER = "other"


class EmailClassification(AgentSchema):
    """邮件分类和优先级节点的结构化输出。"""

    action: EmailAction
    priority: EmailPriority
    category: EmailCategory
    summary: str = Field(min_length=1, max_length=500)
    reason: str = Field(min_length=1, max_length=500)
    confidence: float = Field(ge=0, le=1)


class TaskIntent(AgentSchema):
    """从邮件中提取的一项任务。"""

    title: str = Field(min_length=1, max_length=500)
    description: str = Field(default="", max_length=2000)
    due_at: datetime | None = None
    assignees: list[EmailStr] = Field(default_factory=list, max_length=50)

    @field_validator("due_at")
    @classmethod
    def require_due_at_timezone(cls, value: datetime | None) -> datetime | None:
        """任务截止时间一旦存在，就必须能够表示明确的绝对时间。"""

        if value is not None and value.utcoffset() is None:
            msg = "任务截止时间必须包含时区"
            raise ValueError(msg)
        return value


class MeetingIntent(AgentSchema):
    """从邮件中提取的会议意图和时间完整性。"""

    detected: bool = False
    title: str | None = Field(default=None, max_length=500)
    start_at: datetime | None = None
    end_at: datetime | None = None
    timezone: str | None = Field(default=None, max_length=64)
    attendees: list[EmailStr] = Field(default_factory=list, max_length=100)
    location: str | None = Field(default=None, max_length=500)
    time_information_complete: bool = False
    source_text: str | None = Field(default=None, max_length=1000)

    @field_validator("timezone")
    @classmethod
    def validate_timezone(cls, value: str | None) -> str | None:
        """如果模型给出时区，必须是有效 IANA 时区。"""

        if value is None:
            return None
        try:
            ZoneInfo(value)
        except ZoneInfoNotFoundError as exc:
            msg = f"无效会议时区：{value}"
            raise ValueError(msg) from exc
        return value

    @model_validator(mode="after")
    def validate_time_information(self) -> Self:
        """完整会议时间必须同时具有带时区的开始、结束和时区名称。"""

        for value in (self.start_at, self.end_at):
            if value is not None and value.utcoffset() is None:
                msg = "会议时间必须包含时区"
                raise ValueError(msg)
        if self.start_at is not None and self.end_at is not None:
            if self.start_at >= self.end_at:
                msg = "会议结束时间必须晚于开始时间"
                raise ValueError(msg)
        if self.time_information_complete:
            if not self.detected or not self.start_at or not self.end_at or not self.timezone:
                msg = "完整会议时间需要 detected、start_at、end_at 和 timezone"
                raise ValueError(msg)
        return self


class ExtractedIntent(AgentSchema):
    """任务、会议和待追问信息的结构化提取结果。"""

    tasks: list[TaskIntent] = Field(default_factory=list, max_length=10)
    meeting: MeetingIntent = Field(default_factory=MeetingIntent)
    needs_clarification: bool = False
    missing_information: list[str] = Field(default_factory=list, max_length=20)
    reason: str = Field(min_length=1, max_length=1000)

    @model_validator(mode="after")
    def require_missing_information(self) -> Self:
        """需要追问时必须明确告诉后续节点缺少什么。"""

        if self.needs_clarification and not self.missing_information:
            msg = "需要追问时 missing_information 不能为空"
            raise ValueError(msg)
        return self


class PlanAction(StrEnum):
    """第一版确定性工作流允许模型规划的动作类型。"""

    READ_TOOL = "read_tool"
    GENERATE_DRAFT = "generate_draft"
    REQUEST_CLARIFICATION = "request_clarification"
    IGNORE = "ignore"
    FINALIZE = "finalize"


class ReadOnlyToolName(StrEnum):
    """计划阶段允许引用的只读 MCP 工具。"""

    GET_EMAIL_THREAD = "get_email_thread"
    SEARCH_EMAILS = "search_emails"
    CHECK_AVAILABILITY = "check_availability"
    FIND_AVAILABLE_SLOTS = "find_available_slots"


class GetEmailThreadArguments(AgentSchema):
    """get_email_thread 的精确参数。"""

    thread_id: UUID


class SearchEmailsArguments(AgentSchema):
    """search_emails 的精确参数。"""

    query: str | None = Field(default=None, max_length=200)
    sender: EmailStr | None = None
    status: str | None = Field(default=None, max_length=32)
    offset: int = Field(default=0, ge=0)
    limit: int = Field(default=20, ge=1, le=100)


class CheckAvailabilityArguments(AgentSchema):
    """check_availability 的精确参数。"""

    start_at: datetime
    end_at: datetime
    exclude_event_id: UUID | None = None

    @model_validator(mode="after")
    def validate_time_range(self) -> Self:
        """可用性查询必须使用带时区且有效的时间范围。"""

        if self.start_at.utcoffset() is None or self.end_at.utcoffset() is None:
            msg = "可用性查询时间必须包含时区"
            raise ValueError(msg)
        if self.start_at >= self.end_at:
            msg = "可用性查询结束时间必须晚于开始时间"
            raise ValueError(msg)
        return self


class FindAvailableSlotsArguments(AgentSchema):
    """find_available_slots 的精确参数。"""

    window_start: datetime
    window_end: datetime
    duration_minutes: int = Field(ge=1, le=480)
    step_minutes: int = Field(default=30, ge=5, le=120)
    limit: int = Field(default=5, ge=1, le=20)

    @model_validator(mode="after")
    def validate_window(self) -> Self:
        """候选窗口必须带时区，并至少容纳一个完整会议。"""

        if self.window_start.utcoffset() is None or self.window_end.utcoffset() is None:
            msg = "候选时间窗口必须包含时区"
            raise ValueError(msg)
        if self.window_start >= self.window_end:
            msg = "候选窗口结束时间必须晚于开始时间"
            raise ValueError(msg)
        return self


type ReadOnlyToolArguments = (
    GetEmailThreadArguments
    | SearchEmailsArguments
    | CheckAvailabilityArguments
    | FindAvailableSlotsArguments
)

READ_ONLY_TOOL_ARGUMENT_MODELS: dict[ReadOnlyToolName, type[AgentSchema]] = {
    ReadOnlyToolName.GET_EMAIL_THREAD: GetEmailThreadArguments,
    ReadOnlyToolName.SEARCH_EMAILS: SearchEmailsArguments,
    ReadOnlyToolName.CHECK_AVAILABILITY: CheckAvailabilityArguments,
    ReadOnlyToolName.FIND_AVAILABLE_SLOTS: FindAvailableSlotsArguments,
}


class ReadToolDecision(AgentSchema):
    """合并分析节点选择的一次只读工具调用。"""

    tool_name: ReadOnlyToolName
    tool_arguments: ReadOnlyToolArguments

    @model_validator(mode="after")
    def validate_tool_arguments(self) -> Self:
        """工具名称和参数 Schema 必须严格对应。"""

        expected_model = READ_ONLY_TOOL_ARGUMENT_MODELS[self.tool_name]
        if not isinstance(self.tool_arguments, expected_model):
            msg = f"{self.tool_name.value} 的 tool_arguments 参数结构不正确"
            raise ValueError(msg)
        return self


class IntentPlanDecision(AgentSchema):
    """一次模型调用生成的意图和精简工具决策。"""

    intent: ExtractedIntent
    read_tools: list[ReadToolDecision] = Field(default_factory=list, max_length=4)
    should_generate_draft: bool
    reason: str = Field(min_length=1, max_length=500)


class PlanStep(AgentSchema):
    """一个可审查、可计数的执行计划步骤。"""

    sequence: int = Field(ge=1, le=10)
    action: PlanAction
    description: str = Field(min_length=1, max_length=500)
    tool_name: ReadOnlyToolName | None = None
    tool_arguments: ReadOnlyToolArguments | None = None

    @model_validator(mode="after")
    def validate_tool_step(self) -> Self:
        """只有只读工具步骤可以携带工具名和参数。"""

        if self.action is PlanAction.READ_TOOL and self.tool_name is None:
            msg = "read_tool 步骤必须指定 tool_name"
            raise ValueError(msg)
        if self.action is PlanAction.READ_TOOL and self.tool_name is not None:
            expected_model = READ_ONLY_TOOL_ARGUMENT_MODELS[self.tool_name]
            if not isinstance(self.tool_arguments, expected_model):
                msg = f"{self.tool_name.value} 的 tool_arguments 参数结构不正确"
                raise ValueError(msg)
        if self.action is not PlanAction.READ_TOOL:
            if self.tool_name is not None or self.tool_arguments is not None:
                msg = "非 read_tool 步骤不能携带工具名或工具参数"
                raise ValueError(msg)
        return self


class ExecutionPlan(AgentSchema):
    """模型生成、Python 校验并由 LangGraph 路由的受控计划。"""

    goal: str = Field(min_length=1, max_length=500)
    steps: list[PlanStep] = Field(min_length=1, max_length=10)
    should_generate_draft: bool
    needs_clarification: bool = False

    @model_validator(mode="after")
    def validate_step_order(self) -> Self:
        """步骤编号连续，并确保所有草稿决定与动作保持一致。"""

        sequences = [step.sequence for step in self.steps]
        if sequences != list(range(1, len(sequences) + 1)):
            msg = "计划步骤 sequence 必须从 1 开始连续递增"
            raise ValueError(msg)
        has_draft_action = any(
            step.action in {PlanAction.GENERATE_DRAFT, PlanAction.REQUEST_CLARIFICATION}
            for step in self.steps
        )
        if self.should_generate_draft != has_draft_action:
            msg = "should_generate_draft 必须与草稿或追问步骤保持一致"
            raise ValueError(msg)
        if self.needs_clarification and not self.should_generate_draft:
            msg = "需要追问时必须生成澄清草稿"
            raise ValueError(msg)
        return self

    @property
    def expected_tool_calls(self) -> int:
        """返回计划中的只读工具调用数量。"""

        return sum(step.action is PlanAction.READ_TOOL for step in self.steps)


class DraftPurpose(StrEnum):
    """草稿的业务目的。"""

    REPLY = "reply"
    REMINDER = "reminder"
    CLARIFICATION = "clarification"


class EmailDraft(AgentSchema):
    """尚未发送、可以进入后续审批的邮件草稿。"""

    purpose: DraftPurpose
    recipients: list[EmailStr] = Field(min_length=1, max_length=100)
    cc: list[EmailStr] = Field(default_factory=list, max_length=100)
    subject: str = Field(default="", max_length=500)
    body_text: str = Field(min_length=1, max_length=20000)


class EmailDraftContent(AgentSchema):
    """模型只负责生成的草稿正文；信封字段由 Python 确定。"""

    body_text: str = Field(min_length=1, max_length=20000)


class ToolExecutionResult(AgentSchema):
    """一次受控 MCP 工具调用的最小执行记录。"""

    tool_name: ReadOnlyToolName
    success: bool
    output: JsonValue | None = None
    error_code: str | None = Field(default=None, max_length=100)
    error_message: str | None = Field(default=None, max_length=1000)
    duration_ms: float = Field(ge=0)


class AgentError(AgentSchema):
    """能够写入 State 并用于失败分支的安全错误。"""

    node: str = Field(min_length=1, max_length=100)
    code: str = Field(min_length=1, max_length=100)
    message: str = Field(min_length=1, max_length=1000)
    retryable: bool = False


class ModelUsage(AgentSchema):
    """一次模型调用的 Token 与延迟记录。"""

    operation: str = Field(min_length=1, max_length=100)
    model_name: str = Field(min_length=1, max_length=255)
    input_tokens: int = Field(default=0, ge=0)
    output_tokens: int = Field(default=0, ge=0)
    total_tokens: int = Field(default=0, ge=0)
    latency_ms: float = Field(ge=0)

    @model_validator(mode="after")
    def validate_total_tokens(self) -> Self:
        """提供商返回统计时，总 Token 不应小于输入与输出之和。"""

        if self.total_tokens < self.input_tokens + self.output_tokens:
            msg = "total_tokens 不能小于 input_tokens 与 output_tokens 之和"
            raise ValueError(msg)
        return self


class AgentRunStatus(StrEnum):
    """当前图执行结果状态。"""

    PENDING = "pending"
    RUNNING = "running"
    WAITING_APPROVAL = "waiting_approval"
    DRAFT_READY = "draft_ready"
    IGNORED = "ignored"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class FinalResult(AgentSchema):
    """工作流结束时返回给上层 API 的结构化摘要。"""

    status: AgentRunStatus
    summary: str = Field(min_length=1, max_length=2000)
    draft_ready: bool = False
    requires_future_approval: bool = False
