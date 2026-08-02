"""AgentRun 创建、查询与状态迁移业务。"""

from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.agent_run import AgentRun, AgentRunStatus
from app.models.agent_run_event import AgentRunEventType
from app.models.audit import AuditLog
from app.repositories.agent_run import AgentRunRepository
from app.repositories.audit import AuditRepository
from app.repositories.email import EmailRepository
from app.schemas.agent_run import AgentRunCreate
from app.schemas.agent_run_event import AgentRunEventPayload
from app.services.agent_run_event import AgentRunEventService, event_value
from app.services.exceptions import (
    AgentRunConflictError,
    AgentRunInvalidTransitionError,
    AgentRunNotFoundError,
    EmailThreadNotFoundError,
)

_RUN_TRANSITIONS: dict[AgentRunStatus, frozenset[AgentRunStatus]] = {
    AgentRunStatus.PENDING: frozenset(
        {AgentRunStatus.RUNNING, AgentRunStatus.FAILED, AgentRunStatus.CANCELLED}
    ),
    AgentRunStatus.RUNNING: frozenset(
        {
            AgentRunStatus.WAITING_APPROVAL,
            AgentRunStatus.COMPLETED,
            AgentRunStatus.FAILED,
            AgentRunStatus.CANCELLED,
        }
    ),
    AgentRunStatus.WAITING_APPROVAL: frozenset(
        {
            AgentRunStatus.RUNNING,
            AgentRunStatus.COMPLETED,
            AgentRunStatus.FAILED,
            AgentRunStatus.CANCELLED,
        }
    ),
    AgentRunStatus.COMPLETED: frozenset(),
    AgentRunStatus.FAILED: frozenset(),
    AgentRunStatus.CANCELLED: frozenset(),
}


class AgentRunService:
    """负责 AgentRun 用户隔离、事务和有限状态机。"""

    def __init__(
        self,
        session: AsyncSession,
        run_repository: AgentRunRepository | None = None,
        email_repository: EmailRepository | None = None,
        audit_repository: AuditRepository | None = None,
        event_service: AgentRunEventService | None = None,
    ) -> None:
        self._session = session
        self._runs = run_repository or AgentRunRepository(session)
        self._emails = email_repository or EmailRepository(session)
        self._audits = audit_repository or AuditRepository(session)
        self._events = event_service or AgentRunEventService(
            session,
            run_repository=self._runs,
        )

    async def create_run(
        self,
        *,
        user_id: UUID,
        data: AgentRunCreate,
        request_id: str | None = None,
    ) -> AgentRun:
        """为当前用户存在的邮件线程创建唯一 Graph 执行记录。"""

        try:
            email_thread = await self._emails.get_thread_by_id(
                user_id=user_id,
                thread_id=data.email_thread_id,
            )
            if email_thread is None:
                raise EmailThreadNotFoundError
            if await self._runs.get_by_graph_thread_id(
                user_id=user_id,
                graph_thread_id=data.graph_thread_id,
            ):
                raise AgentRunConflictError

            run = AgentRun(
                user_id=user_id,
                email_thread_id=data.email_thread_id,
                graph_thread_id=data.graph_thread_id,
                workflow_name=data.workflow_name,
                status=AgentRunStatus.PENDING,
                current_node="start",
                result={},
            )
            await self._runs.add(run)
            await self._audits.add(
                AuditLog(
                    user_id=user_id,
                    agent_run_id=run.id,
                    action="agent_run.created",
                    resource_type="agent_run",
                    resource_id=run.id,
                    request_id=request_id,
                    details={
                        "graph_thread_id": data.graph_thread_id,
                        "workflow_name": data.workflow_name.value,
                    },
                )
            )
            await self._events.append(
                user_id=user_id,
                agent_run_id=run.id,
                event_type=AgentRunEventType.RUN_CREATED,
                node_name="start",
                payload=AgentRunEventPayload(
                    status=AgentRunStatus.PENDING.value,
                    current_node="start",
                    message="Agent 执行记录已创建",
                ),
                commit=False,
                run_locked=True,
            )
            await self._session.commit()
            return run
        except (EmailThreadNotFoundError, AgentRunConflictError):
            await self._session.rollback()
            raise
        except IntegrityError as exc:
            await self._session.rollback()
            raise AgentRunConflictError from exc
        except Exception:
            await self._session.rollback()
            raise

    async def get_run(self, *, user_id: UUID, run_id: UUID) -> AgentRun:
        run = await self._runs.get_by_id(user_id=user_id, run_id=run_id)
        if run is None:
            raise AgentRunNotFoundError
        return run

    async def list_runs(
        self,
        *,
        user_id: UUID,
        status: AgentRunStatus | None = None,
        offset: int = 0,
        limit: int = 20,
    ) -> tuple[list[AgentRun], int]:
        """分页读取当前用户自己的 Agent 运行。"""

        items = await self._runs.list_runs(
            user_id=user_id,
            status=status,
            offset=offset,
            limit=limit,
        )
        total = await self._runs.count_runs(user_id=user_id, status=status)
        return items, total

    async def transition_status(
        self,
        *,
        user_id: UUID,
        run_id: UUID,
        target_status: AgentRunStatus,
        current_node: str,
        actor_user_id: UUID | None = None,
        request_id: str | None = None,
        error_code: str | None = None,
        error_message: str | None = None,
        result: dict[str, Any] | None = None,
        input_tokens: int | None = None,
        output_tokens: int | None = None,
        total_tokens: int | None = None,
    ) -> AgentRun:
        """在行锁和状态机约束下迁移一次 AgentRun。"""

        try:
            run = await self._runs.get_for_update(user_id=user_id, run_id=run_id)
            if run is None:
                raise AgentRunNotFoundError
            if target_status not in _RUN_TRANSITIONS[run.status]:
                raise AgentRunInvalidTransitionError

            previous_status = run.status
            run.status = target_status
            run.current_node = current_node
            run.error_code = error_code
            run.error_message = error_message
            if result is not None:
                run.result = result
            if input_tokens is not None:
                run.input_tokens = input_tokens
            if output_tokens is not None:
                run.output_tokens = output_tokens
            if total_tokens is not None:
                run.total_tokens = total_tokens
            now = datetime.now(UTC)
            if target_status is AgentRunStatus.RUNNING and run.started_at is None:
                run.started_at = now
            if target_status in {
                AgentRunStatus.COMPLETED,
                AgentRunStatus.FAILED,
                AgentRunStatus.CANCELLED,
            }:
                run.completed_at = now
            await self._audits.add(
                AuditLog(
                    user_id=user_id,
                    actor_user_id=actor_user_id,
                    agent_run_id=run.id,
                    action="agent_run.status_changed",
                    resource_type="agent_run",
                    resource_id=run.id,
                    request_id=request_id,
                    details={
                        "from": previous_status.value,
                        "to": target_status.value,
                        "current_node": current_node,
                    },
                )
            )
            await self._events.append(
                user_id=user_id,
                agent_run_id=run.id,
                event_type=_event_type_for_status(
                    previous_status=previous_status,
                    target_status=target_status,
                ),
                node_name=current_node,
                payload=AgentRunEventPayload(
                    previous_status=event_value(previous_status),
                    status=event_value(target_status),
                    current_node=current_node,
                    error_code=error_code,
                    message=_event_message_for_status(target_status),
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                    total_tokens=total_tokens,
                ),
                commit=False,
                run_locked=True,
            )
            await self._session.commit()
            return run
        except (AgentRunNotFoundError, AgentRunInvalidTransitionError):
            await self._session.rollback()
            raise
        except Exception:
            await self._session.rollback()
            raise

    async def update_snapshot(
        self,
        *,
        user_id: UUID,
        run_id: UUID,
        current_node: str,
        result: dict[str, Any],
        input_tokens: int,
        output_tokens: int,
        total_tokens: int,
        actor_user_id: UUID | None = None,
        request_id: str | None = None,
    ) -> AgentRun:
        """状态不变时更新 Graph 快照，例如反馈后暂停在下一张审批单。"""

        try:
            run = await self._runs.get_for_update(user_id=user_id, run_id=run_id)
            if run is None:
                raise AgentRunNotFoundError
            run.current_node = current_node
            run.result = result
            run.input_tokens = input_tokens
            run.output_tokens = output_tokens
            run.total_tokens = total_tokens
            await self._audits.add(
                AuditLog(
                    user_id=user_id,
                    actor_user_id=actor_user_id,
                    agent_run_id=run.id,
                    action="agent_run.snapshot_updated",
                    resource_type="agent_run",
                    resource_id=run.id,
                    request_id=request_id,
                    details={
                        "status": run.status.value,
                        "current_node": current_node,
                    },
                )
            )
            if run.status is AgentRunStatus.WAITING_APPROVAL:
                raw_approval_id = result.get("approval_request_id")
                approval_request_id = (
                    UUID(raw_approval_id)
                    if isinstance(raw_approval_id, str)
                    else raw_approval_id
                    if isinstance(raw_approval_id, UUID)
                    else None
                )
                await self._events.append(
                    user_id=user_id,
                    agent_run_id=run.id,
                    event_type=AgentRunEventType.APPROVAL_REQUIRED,
                    node_name=current_node,
                    payload=AgentRunEventPayload(
                        status=run.status.value,
                        current_node=current_node,
                        approval_request_id=approval_request_id,
                        message="工作流已暂停，等待人工审批",
                        input_tokens=input_tokens,
                        output_tokens=output_tokens,
                        total_tokens=total_tokens,
                    ),
                    commit=False,
                    run_locked=True,
                )
            await self._session.commit()
            return run
        except AgentRunNotFoundError:
            await self._session.rollback()
            raise
        except Exception:
            await self._session.rollback()
            raise


def _event_type_for_status(
    *,
    previous_status: AgentRunStatus,
    target_status: AgentRunStatus,
) -> AgentRunEventType:
    if target_status is AgentRunStatus.RUNNING:
        return (
            AgentRunEventType.RUN_STARTED
            if previous_status is AgentRunStatus.PENDING
            else AgentRunEventType.RUN_RESUMED
        )
    if target_status is AgentRunStatus.WAITING_APPROVAL:
        return AgentRunEventType.APPROVAL_REQUIRED
    if target_status is AgentRunStatus.COMPLETED:
        return AgentRunEventType.RUN_COMPLETED
    if target_status is AgentRunStatus.CANCELLED:
        return AgentRunEventType.RUN_CANCELLED
    return AgentRunEventType.RUN_FAILED


def _event_message_for_status(status: AgentRunStatus) -> str:
    return {
        AgentRunStatus.RUNNING: "Agent 工作流开始或恢复执行",
        AgentRunStatus.WAITING_APPROVAL: "工作流已暂停，等待人工审批",
        AgentRunStatus.COMPLETED: "Agent 工作流执行完成",
        AgentRunStatus.FAILED: "Agent 工作流执行失败",
        AgentRunStatus.CANCELLED: "Agent 工作流已取消",
        AgentRunStatus.PENDING: "Agent 工作流等待执行",
    }[status]
