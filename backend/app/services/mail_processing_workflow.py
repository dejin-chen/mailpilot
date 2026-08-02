"""启动完整邮件处理 Graph，并同步 AgentRun 产品状态。"""

import logging
from collections.abc import Callable, Iterator, Mapping
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from typing import Any, Protocol
from uuid import UUID, uuid4

from langgraph.store.base import BaseStore
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.context import AgentRuntimeContext
from app.agent.result import build_persisted_agent_result, sum_model_usage
from app.agent.schemas import AgentRunStatus as GraphRunStatus
from app.agent.workflow import MailProcessingGraph
from app.models.agent_run import (
    AgentRun,
    AgentRunStatus,
    AgentWorkflowName,
)
from app.models.agent_run_event import AgentRunEventType
from app.observability.base import Observability
from app.observability.factory import get_observability
from app.schemas.agent_run import AgentRunCreate
from app.services.agent_run import AgentRunService
from app.services.agent_run_event import AgentRunEventService, build_node_event_payload
from app.services.exceptions import AgentMemoryStoreUnavailableError, AgentWorkflowExecutionError
from app.services.memory_store import MemoryStoreSyncService

logger = logging.getLogger(__name__)


class MailProcessingGraphFactory(Protocol):
    """延迟构建完整 Graph，避免无关审批请求强制要求模型配置。"""

    def __call__(self) -> MailProcessingGraph: ...


@dataclass(frozen=True, slots=True)
class StartedMailProcessing:
    """启动请求返回给 API 的稳定结果。"""

    run: AgentRun
    approval_request_id: UUID | None


SessionFactory = Callable[[], AbstractAsyncContextManager[AsyncSession]]


class MailProcessingWorkflowService:
    """组织 AgentRun 创建、Graph 调用和最终状态同步。"""

    def __init__(
        self,
        session: AsyncSession,
        graph_factory: MailProcessingGraphFactory,
        store: BaseStore,
        observability: Observability | None = None,
    ) -> None:
        self._session = session
        self._graph_factory = graph_factory
        self._store = store
        self._observability = observability or get_observability()

    async def start(
        self,
        *,
        user_id: UUID,
        user_timezone: str,
        email_thread_id: UUID,
        request_id: str,
    ) -> StartedMailProcessing:
        """兼容 Service 调用：创建运行后同步执行到终态或第一次暂停。"""

        run = await self.prepare(
            user_id=user_id,
            email_thread_id=email_thread_id,
            request_id=request_id,
        )
        return await self.execute(
            user_id=user_id,
            user_timezone=user_timezone,
            run_id=run.id,
            request_id=request_id,
        )

    async def prepare(
        self,
        *,
        user_id: UUID,
        email_thread_id: UUID,
        request_id: str,
    ) -> AgentRun:
        """只创建 pending AgentRun，让 HTTP 接口可以尽快返回运行编号。"""

        graph_thread_id = f"mail-processing-{uuid4()}"
        return await AgentRunService(self._session).create_run(
            user_id=user_id,
            data=AgentRunCreate(
                email_thread_id=email_thread_id,
                graph_thread_id=graph_thread_id,
                workflow_name=AgentWorkflowName.MAIL_PROCESSING_V1,
            ),
            request_id=request_id,
        )

    async def execute(
        self,
        *,
        user_id: UUID,
        user_timezone: str,
        run_id: UUID,
        request_id: str,
    ) -> StartedMailProcessing:
        """在独立 Session 中执行已创建的运行，并持续写入逐节点事件。"""

        run_service = AgentRunService(self._session)
        run = await run_service.get_run(user_id=user_id, run_id=run_id)
        graph_thread_id = run.graph_thread_id
        email_thread_id = run.email_thread_id
        run = await run_service.transition_status(
            user_id=user_id,
            run_id=run_id,
            target_status=AgentRunStatus.RUNNING,
            current_node="load_email",
            actor_user_id=user_id,
            request_id=request_id,
        )
        context = AgentRuntimeContext(
            user_id=user_id,
            agent_run_id=run.id,
            request_id=request_id,
            user_timezone=user_timezone,
        )
        config = {"configurable": {"thread_id": graph_thread_id}}
        workflow_name = getattr(
            run,
            "workflow_name",
            AgentWorkflowName.MAIL_PROCESSING_V1,
        )
        with self._observability.trace(
            name="mail_processing.execute",
            user_id=user_id,
            thread_id=graph_thread_id,
            agent_run_id=run.id,
            request_id=request_id,
            input={"email_thread_id": email_thread_id},
            metadata={"workflow_name": workflow_name.value, "resume": False},
        ) as trace:
            try:
                graph = self._graph_factory()
                await MemoryStoreSyncService(
                    self._session,
                    self._store,
                ).sync_user_memories(
                    user_id=user_id,
                    actor_user_id=user_id,
                    agent_run_id=run_id,
                    request_id=request_id,
                )
                result = await stream_graph_with_events(
                    graph=graph,
                    graph_input={"email_thread_id": email_thread_id},
                    config=config,
                    context=context,
                    event_service=AgentRunEventService(self._session),
                )
                trace.update(
                    output=result,
                    metadata={
                        "paused": "__interrupt__" in result,
                        "current_node": str(result.get("current_node", "")),
                    },
                )
            except Exception as exc:
                trace.update(
                    level="ERROR",
                    status_message="完整邮件 Agent 工作流调用失败",
                    metadata={"error_type": type(exc).__name__},
                )
                logger.exception(
                    "完整邮件 Agent 工作流调用失败",
                    extra={
                        "agent_run_id": str(run_id),
                        "error_type": type(exc).__name__,
                    },
                )
                error_code = (
                    exc.code
                    if isinstance(exc, AgentMemoryStoreUnavailableError)
                    else "AGENT_WORKFLOW_EXECUTION_ERROR"
                )
                error_message = (
                    exc.message
                    if isinstance(exc, AgentMemoryStoreUnavailableError)
                    else "Agent 工作流执行失败"
                )
                try:
                    # 前面的事务可能已因数据库或 Store 异常失效，先清理再写失败终态。
                    await self._session.rollback()
                    await run_service.transition_status(
                        user_id=user_id,
                        run_id=run_id,
                        target_status=AgentRunStatus.FAILED,
                        current_node="workflow_crashed",
                        actor_user_id=user_id,
                        request_id=request_id,
                        error_code=error_code,
                        error_message=error_message,
                    )
                except Exception:
                    # 后台入口还会使用一个全新 Session 再兜底，不能让回写异常覆盖原始原因。
                    logger.exception(
                        "Agent 工作流失败状态首次回写失败",
                        extra={"agent_run_id": str(run_id)},
                    )
                if isinstance(exc, AgentMemoryStoreUnavailableError):
                    raise
                raise AgentWorkflowExecutionError from exc

        persisted_result = build_persisted_agent_result(result)
        input_tokens, output_tokens, total_tokens = sum_model_usage(result)
        approval_request_id = result.get("approval_request_id")
        if "__interrupt__" in result:
            await self._session.refresh(run)
            if run.status is AgentRunStatus.WAITING_APPROVAL:
                run = await run_service.update_snapshot(
                    user_id=user_id,
                    run_id=run.id,
                    current_node="wait_for_approval",
                    actor_user_id=user_id,
                    request_id=request_id,
                    result=persisted_result,
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                    total_tokens=total_tokens,
                )
            else:
                run = await run_service.transition_status(
                    user_id=user_id,
                    run_id=run.id,
                    target_status=AgentRunStatus.WAITING_APPROVAL,
                    current_node="wait_for_approval",
                    actor_user_id=user_id,
                    request_id=request_id,
                    result=persisted_result,
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                    total_tokens=total_tokens,
                )
        else:
            target_status, error_code, error_message = self._terminal_status(result)
            run = await run_service.transition_status(
                user_id=user_id,
                run_id=run.id,
                target_status=target_status,
                current_node=str(result.get("current_node", "finalize_failed")),
                actor_user_id=user_id,
                request_id=request_id,
                error_code=error_code,
                error_message=error_message,
                result=persisted_result,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                total_tokens=total_tokens,
            )
        await self._session.refresh(run)
        return StartedMailProcessing(
            run=run,
            approval_request_id=(
                approval_request_id if isinstance(approval_request_id, UUID) else None
            ),
        )

    @staticmethod
    def _terminal_status(
        result: dict[str, object],
    ) -> tuple[AgentRunStatus, str | None, str | None]:
        raw_status = result.get("run_status")
        try:
            status = GraphRunStatus(raw_status)
        except (TypeError, ValueError):
            return (
                AgentRunStatus.FAILED,
                "AGENT_WORKFLOW_STATUS_INVALID",
                "Agent 工作流没有返回有效的结束状态",
            )
        if status is GraphRunStatus.FAILED:
            errors = result.get("errors")
            last_error = errors[-1] if isinstance(errors, list) and errors else None
            error_code = (
                last_error.get("code")
                if isinstance(last_error, Mapping)
                else getattr(last_error, "code", None)
            )
            error_message = (
                last_error.get("message")
                if isinstance(last_error, Mapping)
                else getattr(last_error, "message", None)
            )
            return (
                AgentRunStatus.FAILED,
                str(error_code or result.get("error_code") or "AGENT_WORKFLOW_FAILED"),
                str(
                    error_message
                    or result.get("error_message")
                    or "Agent 工作流执行失败"
                ),
            )
        if status is GraphRunStatus.CANCELLED:
            return AgentRunStatus.CANCELLED, None, None
        if status in {GraphRunStatus.COMPLETED, GraphRunStatus.IGNORED}:
            return AgentRunStatus.COMPLETED, None, None
        return (
            AgentRunStatus.FAILED,
            "AGENT_WORKFLOW_INCOMPLETE",
            "Agent 工作流在非终态结束",
        )


async def run_mail_processing_in_background(
    *,
    session_factory: SessionFactory,
    graph_factory: MailProcessingGraphFactory,
    store: BaseStore,
    user_id: UUID,
    user_timezone: str,
    run_id: UUID,
    request_id: str,
) -> None:
    """使用不依赖 HTTP 请求 Session 的后台任务执行工作流。"""

    failure: Exception | None = None
    async with session_factory() as session:
        try:
            await MailProcessingWorkflowService(
                session,
                graph_factory,
                store,
            ).execute(
                user_id=user_id,
                user_timezone=user_timezone,
                run_id=run_id,
                request_id=request_id,
            )
        except Exception as exc:
            failure = exc
            logger.exception(
                "后台邮件 Agent 工作流结束于异常",
                extra={"agent_run_id": str(run_id)},
            )
    if failure is not None:
        await _persist_background_failure(
            session_factory=session_factory,
            user_id=user_id,
            run_id=run_id,
            request_id=request_id,
            failure=failure,
        )


async def _persist_background_failure(
    *,
    session_factory: SessionFactory,
    user_id: UUID,
    run_id: UUID,
    request_id: str,
    failure: Exception,
) -> None:
    """用全新 Session 兜底写入失败终态，避免页面永久显示“执行中”。"""

    async with session_factory() as recovery_session:
        service = AgentRunService(recovery_session)
        try:
            run = await service.get_run(user_id=user_id, run_id=run_id)
            if run.status in {
                AgentRunStatus.COMPLETED,
                AgentRunStatus.FAILED,
                AgentRunStatus.CANCELLED,
            }:
                return
            is_memory_failure = isinstance(failure, AgentMemoryStoreUnavailableError)
            await service.transition_status(
                user_id=user_id,
                run_id=run_id,
                target_status=AgentRunStatus.FAILED,
                current_node="workflow_crashed",
                actor_user_id=user_id,
                request_id=request_id,
                error_code=(
                    failure.code
                    if is_memory_failure
                    else "AGENT_WORKFLOW_EXECUTION_ERROR"
                ),
                error_message=(
                    failure.message if is_memory_failure else "Agent 工作流执行失败"
                ),
            )
        except Exception:
            logger.exception(
                "Agent 工作流失败状态兜底回写失败",
                extra={"agent_run_id": str(run_id)},
            )


async def stream_graph_with_events(
    *,
    graph: MailProcessingGraph,
    graph_input: object,
    config: dict[str, Any],
    context: AgentRuntimeContext,
    event_service: AgentRunEventService,
) -> dict[str, Any]:
    """消费 LangGraph updates 流，逐节点持久化后读取最终或暂停快照。"""

    async for chunk in graph.astream(
        graph_input,
        config=config,
        context=context,
        stream_mode="updates",
        version="v2",
    ):
        for node_name, update in _iter_node_updates(chunk):
            await event_service.append(
                user_id=context.user_id,
                agent_run_id=context.agent_run_id,
                event_type=AgentRunEventType.NODE_COMPLETED,
                node_name=node_name,
                payload=build_node_event_payload(
                    node_name=node_name,
                    update=update,
                ),
            )

    snapshot = await graph.aget_state(config)
    values = dict(snapshot.values)
    interrupts = [
        interrupt
        for task in getattr(snapshot, "tasks", ())
        for interrupt in getattr(task, "interrupts", ())
    ]
    if interrupts:
        values["__interrupt__"] = interrupts
    return values


def _iter_node_updates(
    chunk: object,
) -> Iterator[tuple[str, Mapping[str, Any] | None]]:
    """兼容 LangGraph v2 统一流格式，并忽略内部控制事件。"""

    if not isinstance(chunk, Mapping):
        return
    raw_data: object = chunk
    chunk_type = chunk.get("type")
    if chunk_type is not None:
        if chunk_type != "updates":
            return
        raw_data = chunk.get("data")
    if not isinstance(raw_data, Mapping):
        return
    for raw_node_name, raw_update in raw_data.items():
        node_name = str(raw_node_name)
        if node_name.startswith("__"):
            continue
        yield (
            node_name,
            raw_update if isinstance(raw_update, Mapping) else None,
        )
