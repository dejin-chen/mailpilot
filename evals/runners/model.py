"""只运行分类、意图和计划组件的真实模型离线评测。"""

from time import perf_counter
from uuid import NAMESPACE_URL, uuid5

from app.agent.prompts import (
    build_classification_messages,
    build_intent_messages,
    build_plan_messages,
)
from app.agent.schemas import (
    EmailAction,
    EmailClassification,
    ExecutionPlan,
    ExtractedIntent,
    ModelUsage,
    PlanAction,
)
from app.agent.security import validate_plan_policy
from app.agent.state import MailAgentState
from app.integrations.llm.client import StructuredLlmClient

from evals.schemas import EvalActual, EvalToolCall, MailEvalCase


class ModelEvaluationRunner:
    """真实调用 OpenAI Compatible 模型，但不访问 MCP、不执行写操作。"""

    def __init__(self, llm_client: StructuredLlmClient) -> None:
        self._llm_client = llm_client

    async def run(self, cases: list[MailEvalCase]) -> list[EvalActual]:
        """顺序执行以控制费用和速率；每条案例最多三次模型调用。"""

        results: list[EvalActual] = []
        for case in cases:
            results.append(await self._run_case(case))
        return results

    async def _run_case(self, case: MailEvalCase) -> EvalActual:
        started_at = perf_counter()
        usages: list[ModelUsage] = []
        state = self._initial_state(case)
        try:
            classification_result = await self._llm_client.ainvoke_structured(
                operation="eval_classify_email",
                messages=build_classification_messages(state),
                schema=EmailClassification,
            )
            classification = classification_result.parsed
            usages.append(classification_result.usage)
            if classification.action is EmailAction.IGNORE:
                return self._actual(
                    case=case,
                    started_at=started_at,
                    usages=usages,
                    classification=classification,
                    intent=None,
                    plan=None,
                    terminal_status="ignored",
                    task_completed=True,
                )

            state["classification"] = classification
            intent_result = await self._llm_client.ainvoke_structured(
                operation="eval_extract_intent",
                messages=build_intent_messages(
                    state,
                    user_timezone=case.input.user_timezone,
                ),
                schema=ExtractedIntent,
            )
            intent = intent_result.parsed
            usages.append(intent_result.usage)
            state["intent"] = intent

            plan_result = await self._llm_client.ainvoke_structured(
                operation="eval_build_plan",
                messages=build_plan_messages(state),
                schema=ExecutionPlan,
            )
            plan = plan_result.parsed
            usages.append(plan_result.usage)
            violation = validate_plan_policy(
                plan=plan,
                classification=classification,
                intent=intent,
                current_thread_id=state["email_thread_id"],
                remaining_tool_calls=4,
            )
            if violation is not None:
                return self._actual(
                    case=case,
                    started_at=started_at,
                    usages=usages,
                    classification=classification,
                    intent=intent,
                    plan=plan,
                    terminal_status="failed",
                    task_completed=False,
                    error_code=violation.code,
                )
            approval_required = self._approval_required(case, plan, intent)
            return self._actual(
                case=case,
                started_at=started_at,
                usages=usages,
                classification=classification,
                intent=intent,
                plan=plan,
                terminal_status=("waiting_approval" if approval_required else "completed"),
                task_completed=True,
                approval_required=approval_required,
            )
        except Exception as exc:
            return EvalActual(
                case_id=case.case_id,
                terminal_status="failed",
                task_completed=False,
                latency_ms=(perf_counter() - started_at) * 1000,
                input_tokens=sum(item.input_tokens for item in usages),
                output_tokens=sum(item.output_tokens for item in usages),
                total_tokens=sum(item.total_tokens for item in usages),
                error_code=type(exc).__name__,
            )

    @staticmethod
    def _initial_state(case: MailEvalCase) -> MailAgentState:
        return MailAgentState(
            email_thread_id=uuid5(NAMESPACE_URL, case.case_id),
            email_subject=case.input.subject,
            email_body=case.input.body,
            sender=str(case.input.sender),
            recipients=[str(item) for item in case.input.recipients],
            cc=[],
            email_sent_at=case.input.sent_at,
            tool_call_count=0,
            max_tool_calls=4,
            model_usages=[],
            tool_results=[],
            errors=[],
        )

    @staticmethod
    def _approval_required(
        case: MailEvalCase,
        plan: ExecutionPlan,
        intent: ExtractedIntent,
    ) -> bool:
        if plan.should_generate_draft:
            return True
        checks_calendar = any(
            step.action is PlanAction.READ_TOOL
            and step.tool_name is not None
            and step.tool_name.value == "check_availability"
            for step in plan.steps
        )
        return bool(
            checks_calendar
            and intent.meeting.time_information_complete
            and case.scenario.calendar_available is True
        )

    @staticmethod
    def _actual(
        *,
        case: MailEvalCase,
        started_at: float,
        usages: list[ModelUsage],
        classification: EmailClassification,
        intent: ExtractedIntent | None,
        plan: ExecutionPlan | None,
        terminal_status: str,
        task_completed: bool,
        approval_required: bool = False,
        error_code: str | None = None,
    ) -> EvalActual:
        return EvalActual(
            case_id=case.case_id,
            action=classification.action,
            priority=classification.priority,
            category=classification.category,
            meeting_detected=intent.meeting.detected if intent else False,
            time_information_complete=(
                intent.meeting.time_information_complete if intent else False
            ),
            needs_clarification=intent.needs_clarification if intent else False,
            tool_calls=[
                EvalToolCall(name=step.tool_name.value)
                for step in (plan.steps if plan else [])
                if step.action is PlanAction.READ_TOOL and step.tool_name is not None
            ],
            approval_required=approval_required,
            # 模型评测不执行写工具；这里验证的是危险意图是否被判定为需审批。
            approval_intercepted=approval_required,
            terminal_status=terminal_status,
            task_completed=task_completed,
            latency_ms=(perf_counter() - started_at) * 1000,
            input_tokens=sum(item.input_tokens for item in usages),
            output_tokens=sum(item.output_tokens for item in usages),
            total_tokens=sum(item.total_tokens for item in usages),
            error_code=error_code,
        )
