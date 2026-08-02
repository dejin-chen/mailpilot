"""无需网络和模型额度的参考契约重放。"""

from evals.schemas import EvalActual, EvalToolCall, MailEvalCase


class ReferenceEvaluationRunner:
    """用人工标签驱动 Fake 依赖，验证指标、报告和分支契约。"""

    async def run(self, cases: list[MailEvalCase]) -> list[EvalActual]:
        """生成可重复的参考结果，不把 100% 分数冒充真实模型成绩。"""

        return [self._run_case(case) for case in cases]

    @staticmethod
    def _run_case(case: MailEvalCase) -> EvalActual:
        tool_success = not case.scenario.tool_failure
        executes_write = case.expected.requires_approval and case.scenario.approval_decision in {
            "approved",
            "modified",
        }
        write_tool_name = (
            "create_event"
            if (
                case.expected.meeting_detected
                and case.expected.time_information_complete
                and case.scenario.calendar_available is True
            )
            else "send_email"
        )
        return EvalActual(
            case_id=case.case_id,
            action=case.expected.action,
            priority=case.expected.priority,
            category=case.expected.category,
            meeting_detected=case.expected.meeting_detected,
            time_information_complete=case.expected.time_information_complete,
            needs_clarification=case.expected.needs_clarification,
            tool_calls=[
                EvalToolCall(
                    name=name,
                    success=tool_success,
                    latency_ms=1.0,
                )
                for name in case.expected.expected_tools
            ],
            write_tool_call=(
                EvalToolCall(
                    name=write_tool_name,
                    success=case.expected.expected_terminal_status == "completed",
                    latency_ms=1.0,
                )
                if executes_write
                else None
            ),
            approval_required=case.expected.requires_approval,
            approval_intercepted=case.expected.requires_approval,
            terminal_status=case.expected.expected_terminal_status,
            task_completed=case.expected.task_completed,
            latency_ms=1.0 + len(case.expected.expected_tools),
            error_code=("MCP_TOOL_EXECUTION_ERROR" if case.scenario.tool_failure else None),
        )
