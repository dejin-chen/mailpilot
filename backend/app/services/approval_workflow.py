"""审批决定落库后恢复正确版本 LangGraph 的应用编排 Service。"""

import logging
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.types import Command
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.approval_graph import build_approval_gate_graph
from app.agent.approval_schemas import (
    ApprovalGateStatus,
    ApprovalResumeSignal,
    WriteExecutionStatus,
)
from app.agent.context import AgentRuntimeContext
from app.agent.nodes.approval import ServiceApprovalRequestCreator
from app.agent.nodes.execute_write import ApprovedActionExecutor
from app.agent.result import build_persisted_agent_result, sum_model_usage
from app.agent.schemas import AgentRunStatus as GraphRunStatus
from app.models.agent_run import (
    AgentRun,
    AgentRunStatus,
    AgentWorkflowName,
)
from app.models.agent_run_event import AgentRunEventType
from app.models.approval import ApprovalRequest, ApprovalStatus
from app.observability.base import Observability
from app.observability.factory import get_observability
from app.schemas.agent_run_event import AgentRunEventPayload
from app.schemas.approval import ApprovalDecision
from app.services.agent_run import AgentRunService
from app.services.agent_run_event import AgentRunEventService
from app.services.approval import ApprovalService
from app.services.exceptions import (
    AgentCheckpointConflictError,
    AgentCheckpointNotFoundError,
    AgentResumeError,
)
from app.services.mail_processing_workflow import (
    MailProcessingGraphFactory,
    stream_graph_with_events,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class ApprovalWorkflowResult:
    """一次审批决定和 Graph 恢复的结果。"""

    approval: ApprovalRequest
    agent_run: AgentRun
    graph_resumed: bool
    already_resumed: bool
    next_approval_request_id: UUID | None = None


@dataclass(frozen=True, slots=True)
class GraphResumeOutcome:
    """Graph 恢复后的状态、执行摘要和下一张审批单。"""

    already_resumed: bool
    values: dict[str, Any]
    waiting_for_approval: bool
    next_approval_request_id: UUID | None
    execution_status: WriteExecutionStatus | None
    error_code: str | None
    error_message: str | None


class ApprovalWorkflowService:
    """按“先保存决定、再恢复正确 Graph”的顺序组织跨组件操作。"""

    def __init__(
        self,
        session: AsyncSession,
        checkpointer: BaseCheckpointSaver,
        executor: ApprovedActionExecutor,
        mail_processing_graph_factory: MailProcessingGraphFactory | None = None,
        observability: Observability | None = None,
    ) -> None:
        self._session = session
        self._checkpointer = checkpointer
        self._executor = executor
        self._mail_processing_graph_factory = mail_processing_graph_factory
        self._observability = observability or get_observability()

    async def decide_and_resume(
        self,
        *,
        user_id: UUID,
        user_timezone: str,
        approval_id: UUID,
        decision: ApprovalDecision,
        request_id: str,
    ) -> ApprovalWorkflowResult:
        """持久化用户决定，使用同一 thread_id 恢复对应版本工作流。"""

        approval = await ApprovalService(self._session).decide(
            user_id=user_id,
            approval_id=approval_id,
            actor_user_id=user_id,
            decision=decision,
            request_id=request_id,
        )
        run_service = AgentRunService(self._session)
        agent_run = await run_service.get_run(
            user_id=user_id,
            run_id=approval.agent_run_id,
        )
        resume_outcome = await self._resume_graph(
            user_id=user_id,
            user_timezone=user_timezone,
            approval=approval,
            agent_run=agent_run,
            decision_status=decision.status,
            request_id=request_id,
        )

        if agent_run.workflow_name is AgentWorkflowName.MAIL_PROCESSING_V1:
            agent_run = await self._sync_mail_processing_run(
                run_service=run_service,
                user_id=user_id,
                agent_run=agent_run,
                outcome=resume_outcome,
                request_id=request_id,
            )
        else:
            agent_run = await self._sync_approval_gate_run(
                run_service=run_service,
                user_id=user_id,
                agent_run=agent_run,
                decision=decision,
                outcome=resume_outcome,
                request_id=request_id,
            )

        # 上面可能经历多个独立事务；序列化前显式刷新，避免隐式异步 IO。
        await self._session.refresh(approval)
        await self._session.refresh(agent_run)
        return ApprovalWorkflowResult(
            approval=approval,
            agent_run=agent_run,
            graph_resumed=not resume_outcome.already_resumed,
            already_resumed=resume_outcome.already_resumed,
            next_approval_request_id=resume_outcome.next_approval_request_id,
        )

    async def _sync_mail_processing_run(
        self,
        *,
        run_service: AgentRunService,
        user_id: UUID,
        agent_run: AgentRun,
        outcome: GraphResumeOutcome,
        request_id: str,
    ) -> AgentRun:
        """把完整 Graph 的暂停或终态同步为产品层 AgentRun。"""

        if outcome.already_resumed and agent_run.status in {
            AgentRunStatus.COMPLETED,
            AgentRunStatus.FAILED,
            AgentRunStatus.CANCELLED,
        }:
            return agent_run

        result = build_persisted_agent_result(outcome.values)
        input_tokens, output_tokens, total_tokens = sum_model_usage(outcome.values)
        current_node = str(outcome.values.get("current_node", "finalize_failed"))
        if outcome.waiting_for_approval:
            if agent_run.status is AgentRunStatus.WAITING_APPROVAL:
                return await run_service.update_snapshot(
                    user_id=user_id,
                    run_id=agent_run.id,
                    current_node="wait_for_approval",
                    result=result,
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                    total_tokens=total_tokens,
                    actor_user_id=user_id,
                    request_id=request_id,
                )
            return await run_service.transition_status(
                user_id=user_id,
                run_id=agent_run.id,
                target_status=AgentRunStatus.WAITING_APPROVAL,
                current_node="wait_for_approval",
                actor_user_id=user_id,
                request_id=request_id,
                result=result,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                total_tokens=total_tokens,
            )

        graph_status = self._graph_status(outcome.values)
        if graph_status is GraphRunStatus.COMPLETED:
            target_status = AgentRunStatus.COMPLETED
            error_code = None
            error_message = None
        elif graph_status is GraphRunStatus.CANCELLED:
            target_status = AgentRunStatus.CANCELLED
            error_code = None
            error_message = None
        elif graph_status is GraphRunStatus.FAILED or outcome.execution_status in {
            WriteExecutionStatus.FAILED,
            WriteExecutionStatus.UNCERTAIN,
        }:
            target_status = AgentRunStatus.FAILED
            error_code = outcome.error_code or "AGENT_WORKFLOW_FAILED"
            error_message = outcome.error_message or "Agent 工作流执行失败"
        else:
            raise AgentResumeError
        if agent_run.status is target_status:
            return await run_service.update_snapshot(
                user_id=user_id,
                run_id=agent_run.id,
                current_node=current_node,
                result=result,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                total_tokens=total_tokens,
                actor_user_id=user_id,
                request_id=request_id,
            )
        return await run_service.transition_status(
            user_id=user_id,
            run_id=agent_run.id,
            target_status=target_status,
            current_node=current_node,
            actor_user_id=user_id,
            request_id=request_id,
            error_code=error_code,
            error_message=error_message,
            result=result,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            total_tokens=total_tokens,
        )

    async def _sync_approval_gate_run(
        self,
        *,
        run_service: AgentRunService,
        user_id: UUID,
        agent_run: AgentRun,
        decision: ApprovalDecision,
        outcome: GraphResumeOutcome,
        request_id: str,
    ) -> AgentRun:
        """兼容 5.2～5.4 已经暂停的独立审批闸门运行。"""

        if outcome.execution_status in {
            WriteExecutionStatus.FAILED,
            WriteExecutionStatus.UNCERTAIN,
        }:
            if agent_run.status is not AgentRunStatus.FAILED:
                return await run_service.transition_status(
                    user_id=user_id,
                    run_id=agent_run.id,
                    target_status=AgentRunStatus.FAILED,
                    current_node="write_action_failed",
                    actor_user_id=user_id,
                    request_id=request_id,
                    error_code=outcome.error_code,
                    error_message=outcome.error_message,
                )
            return agent_run

        if decision.status is ApprovalStatus.REJECTED:
            return agent_run
        if agent_run.status is AgentRunStatus.WAITING_APPROVAL:
            if decision.status is ApprovalStatus.FEEDBACK_REQUESTED:
                current_node = "approval_feedback_received"
            elif outcome.execution_status is WriteExecutionStatus.SUCCEEDED:
                current_node = "write_action_executed"
            else:
                current_node = "approval_resumed"
            return await run_service.transition_status(
                user_id=user_id,
                run_id=agent_run.id,
                target_status=AgentRunStatus.RUNNING,
                current_node=current_node,
                actor_user_id=user_id,
                request_id=request_id,
            )
        if agent_run.status is not AgentRunStatus.RUNNING:
            return await run_service.transition_status(
                user_id=user_id,
                run_id=agent_run.id,
                target_status=AgentRunStatus.RUNNING,
                current_node="approval_resumed",
                actor_user_id=user_id,
                request_id=request_id,
            )
        return agent_run

    async def _resume_graph(
        self,
        *,
        user_id: UUID,
        user_timezone: str,
        approval: ApprovalRequest,
        agent_run: AgentRun,
        decision_status: ApprovalStatus,
        request_id: str,
    ) -> GraphResumeOutcome:
        graph = self._build_graph(agent_run)
        config = {"configurable": {"thread_id": agent_run.graph_thread_id}}
        try:
            snapshot = await graph.aget_state(config)
        except Exception as exc:
            logger.exception(
                "读取审批工作流存档失败",
                extra={
                    "agent_run_id": str(agent_run.id),
                    "approval_request_id": str(approval.id),
                    "error_type": type(exc).__name__,
                },
            )
            raise AgentResumeError from exc

        if not snapshot.values:
            raise AgentCheckpointNotFoundError
        checkpoint_approval_id = snapshot.values.get("approval_request_id")
        if checkpoint_approval_id != approval.id:
            if self._is_previous_feedback_already_resumed(
                values=snapshot.values,
                approval_id=approval.id,
                decision_status=decision_status,
            ):
                return self._resume_outcome(snapshot.values, already_resumed=True)
            raise AgentCheckpointConflictError

        if self._gate_status(snapshot.values) is ApprovalGateStatus.RESUMED:
            if self._approval_status(snapshot.values) is not decision_status:
                raise AgentCheckpointConflictError
            return self._resume_outcome(snapshot.values, already_resumed=True)

        signal = ApprovalResumeSignal(
            approval_request_id=approval.id,
            status=decision_status,
            feedback=(
                approval.feedback if decision_status is ApprovalStatus.FEEDBACK_REQUESTED else None
            ),
        )
        context = AgentRuntimeContext(
            user_id=user_id,
            agent_run_id=agent_run.id,
            request_id=request_id,
            user_timezone=user_timezone,
        )
        with self._observability.trace(
            name="mail_processing.resume",
            user_id=user_id,
            thread_id=agent_run.graph_thread_id,
            agent_run_id=agent_run.id,
            request_id=request_id,
            input={
                "approval_request_id": approval.id,
                "decision_status": decision_status.value,
            },
            metadata={
                "workflow_name": agent_run.workflow_name.value,
                "resume": True,
            },
        ) as trace:
            try:
                if agent_run.workflow_name is AgentWorkflowName.MAIL_PROCESSING_V1:
                    await AgentRunEventService(self._session).append(
                        user_id=user_id,
                        agent_run_id=agent_run.id,
                        event_type=AgentRunEventType.RUN_RESUMED,
                        node_name="wait_for_approval",
                        payload=AgentRunEventPayload(
                            status=agent_run.status.value,
                            current_node="wait_for_approval",
                            approval_request_id=approval.id,
                            message="用户已处理审批，工作流从暂停点恢复",
                        ),
                    )
                    result = await stream_graph_with_events(
                        graph=graph,
                        graph_input=Command(resume=signal.model_dump(mode="json")),
                        config=config,
                        context=context,
                        event_service=AgentRunEventService(self._session),
                    )
                else:
                    result = await graph.ainvoke(
                        Command(resume=signal.model_dump(mode="json")),
                        config=config,
                        context=context,
                    )
                trace.update(
                    output=result,
                    metadata={
                        "paused_again": "__interrupt__" in result,
                        "current_node": str(result.get("current_node", "")),
                    },
                )
            except Exception as exc:
                trace.update(
                    level="ERROR",
                    status_message="恢复审批工作流失败",
                    metadata={"error_type": type(exc).__name__},
                )
                logger.exception(
                    "恢复审批工作流失败",
                    extra={
                        "agent_run_id": str(agent_run.id),
                        "approval_request_id": str(approval.id),
                        "error_type": type(exc).__name__,
                    },
                )
                raise AgentResumeError from exc

        if "__interrupt__" in result:
            if self._gate_status(result) is not ApprovalGateStatus.WAITING_APPROVAL:
                raise AgentResumeError
            return self._resume_outcome(result, already_resumed=False)
        if (
            self._gate_status(result) is not ApprovalGateStatus.RESUMED
            or self._approval_status(result) is not decision_status
        ):
            raise AgentResumeError
        return self._resume_outcome(result, already_resumed=False)

    def _build_graph(self, agent_run: AgentRun):
        """旧运行恢复旧 Graph，新运行恢复完整 Graph。"""

        if agent_run.workflow_name is AgentWorkflowName.MAIL_PROCESSING_V1:
            if self._mail_processing_graph_factory is None:
                raise AgentResumeError
            return self._mail_processing_graph_factory()
        return build_approval_gate_graph(
            creator=ServiceApprovalRequestCreator(),
            executor=self._executor,
            checkpointer=self._checkpointer,
        )

    @staticmethod
    def _is_previous_feedback_already_resumed(
        *,
        values: dict[str, Any],
        approval_id: UUID,
        decision_status: ApprovalStatus,
    ) -> bool:
        """反馈恢复后会出现新审批 ID，仍应识别旧决定的幂等重试。"""

        if (
            decision_status is not ApprovalStatus.FEEDBACK_REQUESTED
            or ApprovalWorkflowService._gate_status(values)
            is not ApprovalGateStatus.WAITING_APPROVAL
        ):
            return False
        signal = values.get("resume_signal")
        try:
            validated = ApprovalResumeSignal.model_validate(signal)
        except Exception:
            return False
        return validated.approval_request_id == approval_id and validated.status is decision_status

    @staticmethod
    def _graph_status(values: dict[str, Any]) -> GraphRunStatus | None:
        value = values.get("run_status")
        if isinstance(value, GraphRunStatus):
            return value
        try:
            return GraphRunStatus(value) if isinstance(value, str) else None
        except ValueError:
            return None

    @staticmethod
    def _gate_status(values: dict[str, Any]) -> ApprovalGateStatus | None:
        value = values.get("gate_status")
        if isinstance(value, ApprovalGateStatus):
            return value
        try:
            return ApprovalGateStatus(value) if isinstance(value, str) else None
        except ValueError:
            return None

    @staticmethod
    def _approval_status(values: dict[str, Any]) -> ApprovalStatus | None:
        value = values.get("approval_status")
        if isinstance(value, ApprovalStatus):
            return value
        try:
            return ApprovalStatus(value) if isinstance(value, str) else None
        except ValueError:
            return None

    @staticmethod
    def _resume_outcome(
        values: dict[str, Any],
        *,
        already_resumed: bool,
    ) -> GraphResumeOutcome:
        raw_status = values.get("execution_status")
        execution_status = (
            raw_status
            if isinstance(raw_status, WriteExecutionStatus)
            else WriteExecutionStatus(raw_status)
            if isinstance(raw_status, str)
            else None
        )
        error_code = values.get("error_code")
        error_message = values.get("error_message")
        next_approval_id = values.get("approval_request_id")
        waiting = (
            ApprovalWorkflowService._gate_status(values) is ApprovalGateStatus.WAITING_APPROVAL
        )
        return GraphResumeOutcome(
            already_resumed=already_resumed,
            values=values,
            waiting_for_approval=waiting,
            next_approval_request_id=(
                next_approval_id if waiting and isinstance(next_approval_id, UUID) else None
            ),
            execution_status=execution_status,
            error_code=error_code if isinstance(error_code, str) else None,
            error_message=error_message if isinstance(error_message, str) else None,
        )
