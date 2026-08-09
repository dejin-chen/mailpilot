"""评测数据、单例结果和汇总报告的严格 Schema。"""

from datetime import datetime
from typing import Literal

from app.agent.schemas import EmailAction, EmailCategory, EmailPriority
from pydantic import BaseModel, ConfigDict, EmailStr, Field, model_validator


class EvalSchema(BaseModel):
    """所有评测数据拒绝未知字段，防止标签拼错后被静默忽略。"""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class EvalEmailInput(EvalSchema):
    """一封可以独立重放的测试邮件。"""

    subject: str = Field(min_length=1, max_length=500)
    body: str = Field(min_length=1, max_length=5000)
    sender: EmailStr
    recipients: list[EmailStr] = Field(min_length=1, max_length=20)
    sent_at: datetime
    user_timezone: str = "Asia/Shanghai"


class EvalExpected(EvalSchema):
    """人工标注的 Ground Truth，不由被测模型生成。"""

    action: EmailAction
    priority: EmailPriority
    category: EmailCategory
    meeting_detected: bool
    time_information_complete: bool
    needs_clarification: bool
    expected_tools: list[str] = Field(default_factory=list, max_length=4)
    requires_approval: bool
    expected_terminal_status: Literal[
        "ignored",
        "completed",
        "waiting_approval",
        "cancelled",
        "failed",
    ]
    task_completed: bool = True
    draft_expectation: str | None = Field(default=None, max_length=500)


class EvalScenario(EvalSchema):
    """控制 Fake 环境的日历、工具、审批和恢复分支。"""

    calendar_available: bool | None = None
    tool_failure: bool = False
    approval_decision: Literal[
        "not_required",
        "pending",
        "approved",
        "rejected",
        "modified",
        "feedback",
    ] = "not_required"
    prompt_injection: bool = False
    memory_isolation: bool = False
    duplicate_resume: bool = False


class MailEvalCase(EvalSchema):
    """一条可版本管理的邮件 Agent 评测案例。"""

    case_id: str = Field(pattern=r"^MP-\d{3}$")
    title: str = Field(min_length=1, max_length=200)
    category_group: Literal[
        "classification",
        "meeting",
        "tool",
        "approval",
        "security",
        "recovery",
    ]
    input: EvalEmailInput
    expected: EvalExpected
    scenario: EvalScenario = Field(default_factory=EvalScenario)
    tags: list[str] = Field(min_length=1, max_length=20)

    @model_validator(mode="after")
    def validate_approval_scenario(self) -> "MailEvalCase":
        """需要审批的案例必须声明审批动作，避免标签互相矛盾。"""

        if self.expected.requires_approval:
            if self.scenario.approval_decision == "not_required":
                msg = "requires_approval=true 时必须声明 approval_decision"
                raise ValueError(msg)
        elif self.scenario.approval_decision != "not_required":
            msg = "无需审批的案例不能声明审批决定"
            raise ValueError(msg)
        return self


class EvalToolCall(EvalSchema):
    """评测过程中观察到的一次工具调用。"""

    name: str = Field(min_length=1, max_length=100)
    success: bool | None = None
    latency_ms: float = Field(default=0, ge=0)


class EvalActual(EvalSchema):
    """被测系统针对一条案例产生的结构化观察结果。"""

    case_id: str
    action: EmailAction | None = None
    priority: EmailPriority | None = None
    category: EmailCategory | None = None
    meeting_detected: bool | None = None
    time_information_complete: bool | None = None
    needs_clarification: bool | None = None
    tool_calls: list[EvalToolCall] = Field(default_factory=list)
    write_tool_call: EvalToolCall | None = None
    approval_required: bool = False
    approval_intercepted: bool = False
    terminal_status: str
    task_completed: bool
    latency_ms: float = Field(ge=0)
    input_tokens: int = Field(default=0, ge=0)
    output_tokens: int = Field(default=0, ge=0)
    total_tokens: int = Field(default=0, ge=0)
    draft_text: str | None = None
    error_code: str | None = None


class EvalCaseResult(EvalSchema):
    """标签与实际结果逐字段比较后的单例成绩。"""

    case_id: str
    title: str
    classification_correct: bool
    priority_correct: bool
    category_correct: bool
    meeting_intent_correct: bool
    clarification_correct: bool
    tool_selection_correct: bool
    expected_tool_count: int = Field(ge=0)
    expected_requires_approval: bool
    approval_interception_correct: bool
    terminal_status_correct: bool
    task_completed: bool
    task_outcome_correct: bool
    actual: EvalActual


class MetricValue(EvalSchema):
    """带分子分母的指标，避免只显示一个无法解释的百分比。"""

    numerator: float = Field(ge=0)
    denominator: int = Field(ge=0)
    value: float = Field(ge=0)


class EvaluationSummary(EvalSchema):
    """项目要求的八项核心指标。"""

    case_count: int = Field(ge=0)
    classification_accuracy: MetricValue
    priority_accuracy: MetricValue
    tool_selection_accuracy: MetricValue
    tool_positive_selection_accuracy: MetricValue
    approval_interception_rate: MetricValue
    tool_call_success_rate: MetricValue
    task_completion_rate: MetricValue
    task_outcome_accuracy: MetricValue
    average_latency_ms: float = Field(ge=0)
    median_latency_ms: float = Field(ge=0)
    p95_latency_ms: float = Field(ge=0)
    total_tokens: int = Field(ge=0)
    average_tokens: float = Field(ge=0)
    human_intervention_rate: MetricValue


class EvaluationReport(EvalSchema):
    """一次可保存、可比较的完整评测报告。"""

    report_name: str
    mode: Literal["reference", "model"]
    dataset_version: str
    generated_at: datetime
    summary: EvaluationSummary
    cases: list[EvalCaseResult]
    supplemental_metrics: dict[str, float] = Field(default_factory=dict)
    notes: list[str] = Field(default_factory=list)
