"""审批创建、幂等复用和用户决定状态机。"""

from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from pydantic import ValidationError
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.agent_run import AgentRunStatus
from app.models.approval import ApprovalRequest, ApprovalStatus
from app.models.audit import AuditLog
from app.repositories.agent_run import AgentRunRepository
from app.repositories.approval import ApprovalRepository
from app.repositories.audit import AuditRepository
from app.schemas.approval import (
    WRITE_ACTION_ARGUMENT_MODELS,
    ApprovalDecision,
    ApprovalRequestCreate,
)
from app.services.exceptions import (
    AgentRunInvalidTransitionError,
    AgentRunNotFoundError,
    ApprovalAlreadyDecidedError,
    ApprovalArgumentsInvalidError,
    ApprovalRequestConflictError,
    ApprovalRequestNotFoundError,
)


@dataclass(frozen=True, slots=True)
class ApprovalRequestPage:
    """审批列表的 Service 分页结果。"""

    items: list[ApprovalRequest]
    total: int
    offset: int
    limit: int


class ApprovalService:
    """保证审批属于当前用户、决定不可重复且审计与状态同事务提交。"""

    def __init__(
        self,
        session: AsyncSession,
        approval_repository: ApprovalRepository | None = None,
        run_repository: AgentRunRepository | None = None,
        audit_repository: AuditRepository | None = None,
    ) -> None:
        self._session = session
        self._approvals = approval_repository or ApprovalRepository(session)
        self._runs = run_repository or AgentRunRepository(session)
        self._audits = audit_repository or AuditRepository(session)

    async def create_request(
        self,
        *,
        user_id: UUID,
        data: ApprovalRequestCreate,
        request_id: str | None = None,
    ) -> ApprovalRequest:
        """幂等创建审批并把对应 AgentRun 标记为等待审批。"""

        existing = await self._approvals.get_by_idempotency_key(
            user_id=user_id,
            idempotency_key=data.idempotency_key,
        )
        if existing is not None:
            if self._matches_create(existing, data):
                return existing
            raise ApprovalRequestConflictError

        try:
            run = await self._runs.get_for_update(
                user_id=user_id,
                run_id=data.agent_run_id,
            )
            if run is None:
                raise AgentRunNotFoundError
            creating_next_version = (
                run.status is AgentRunStatus.WAITING_APPROVAL and data.version > 1
            )
            if run.status is not AgentRunStatus.RUNNING and not creating_next_version:
                raise AgentRunInvalidTransitionError

            approval = ApprovalRequest(
                user_id=user_id,
                agent_run_id=data.agent_run_id,
                action=data.action,
                status=ApprovalStatus.PENDING,
                proposed_arguments=dict(data.proposed_arguments),
                idempotency_key=data.idempotency_key,
                version=data.version,
            )
            await self._approvals.add(approval)
            run.status = AgentRunStatus.WAITING_APPROVAL
            run.current_node = "waiting_approval"
            await self._audits.add(
                AuditLog(
                    user_id=user_id,
                    agent_run_id=run.id,
                    approval_request_id=approval.id,
                    action="approval.created",
                    resource_type="approval_request",
                    resource_id=approval.id,
                    request_id=request_id,
                    details={
                        "approval_action": approval.action.value,
                        "version": approval.version,
                    },
                )
            )
            await self._session.commit()
            return approval
        except (
            AgentRunNotFoundError,
            AgentRunInvalidTransitionError,
            ApprovalRequestConflictError,
        ):
            await self._session.rollback()
            raise
        except IntegrityError as exc:
            await self._session.rollback()
            existing = await self._approvals.get_by_idempotency_key(
                user_id=user_id,
                idempotency_key=data.idempotency_key,
            )
            if existing is not None and self._matches_create(existing, data):
                return existing
            raise ApprovalRequestConflictError from exc
        except Exception:
            await self._session.rollback()
            raise

    async def decide(
        self,
        *,
        user_id: UUID,
        approval_id: UUID,
        actor_user_id: UUID,
        decision: ApprovalDecision,
        request_id: str | None = None,
    ) -> ApprovalRequest:
        """只允许 pending 审批被决定一次，并在同一事务写审计。"""

        try:
            approval = await self._approvals.get_for_update(
                user_id=user_id,
                approval_id=approval_id,
            )
            if approval is None:
                raise ApprovalRequestNotFoundError
            modified_arguments = self._normalize_modified_arguments(
                approval=approval,
                decision=decision,
            )
            if approval.status is not ApprovalStatus.PENDING:
                if self._matches_decision(
                    approval,
                    actor_user_id=actor_user_id,
                    decision=decision,
                    modified_arguments=modified_arguments,
                ):
                    # 相同决定重试时只释放行锁，不重复写审计。
                    await self._session.commit()
                    return approval
                raise ApprovalAlreadyDecidedError

            run = await self._runs.get_for_update(
                user_id=user_id,
                run_id=approval.agent_run_id,
            )
            if run is None:
                raise AgentRunNotFoundError

            approval.status = decision.status
            approval.modified_arguments = modified_arguments
            approval.feedback = decision.feedback
            approval.decided_by_user_id = actor_user_id
            approval.decided_at = datetime.now(UTC)
            if decision.status is ApprovalStatus.REJECTED:
                run.status = AgentRunStatus.CANCELLED
                run.current_node = "approval_rejected"
                run.completed_at = approval.decided_at

            await self._audits.add(
                AuditLog(
                    user_id=user_id,
                    actor_user_id=actor_user_id,
                    agent_run_id=run.id,
                    approval_request_id=approval.id,
                    action=f"approval.{decision.status.value}",
                    resource_type="approval_request",
                    resource_id=approval.id,
                    request_id=request_id,
                    details={
                        "approval_action": approval.action.value,
                        "modified": decision.modified_arguments is not None,
                        "has_feedback": decision.feedback is not None,
                    },
                )
            )
            await self._session.commit()
            return approval
        except (
            AgentRunNotFoundError,
            ApprovalArgumentsInvalidError,
            ApprovalAlreadyDecidedError,
            ApprovalRequestNotFoundError,
        ):
            await self._session.rollback()
            raise
        except Exception:
            await self._session.rollback()
            raise

    async def get_request(
        self,
        *,
        user_id: UUID,
        approval_id: UUID,
    ) -> ApprovalRequest:
        approval = await self._approvals.get_by_id(
            user_id=user_id,
            approval_id=approval_id,
        )
        if approval is None:
            raise ApprovalRequestNotFoundError
        return approval

    async def get_request_for_admin(self, *, approval_id: UUID) -> ApprovalRequest:
        """管理员只读查询任意用户审批，决定操作仍走普通用户隔离方法。"""

        approval = await self._approvals.get_by_id_for_admin(approval_id=approval_id)
        if approval is None:
            raise ApprovalRequestNotFoundError
        return approval

    async def list_request_page(
        self,
        *,
        user_id: UUID,
        status: ApprovalStatus | None = None,
        offset: int = 0,
        limit: int = 20,
    ) -> ApprovalRequestPage:
        """返回当前用户自己的审批分页。"""

        items = await self._approvals.list_requests(
            user_id=user_id,
            status=status,
            offset=offset,
            limit=limit,
        )
        total = await self._approvals.count_requests(user_id=user_id, status=status)
        return ApprovalRequestPage(items=items, total=total, offset=offset, limit=limit)

    async def list_request_page_for_admin(
        self,
        *,
        status: ApprovalStatus | None = None,
        offset: int = 0,
        limit: int = 20,
    ) -> ApprovalRequestPage:
        """返回管理员可见的全系统审批分页。"""

        items = await self._approvals.list_requests_for_admin(
            status=status,
            offset=offset,
            limit=limit,
        )
        total = await self._approvals.count_requests_for_admin(status=status)
        return ApprovalRequestPage(items=items, total=total, offset=offset, limit=limit)

    @staticmethod
    def _matches_create(
        existing: ApprovalRequest,
        data: ApprovalRequestCreate,
    ) -> bool:
        return (
            existing.agent_run_id == data.agent_run_id
            and existing.action is data.action
            and existing.proposed_arguments == data.proposed_arguments
            and existing.version == data.version
        )

    @staticmethod
    def _normalize_modified_arguments(
        *,
        approval: ApprovalRequest,
        decision: ApprovalDecision,
    ) -> dict[str, object] | None:
        if decision.modified_arguments is None:
            return None
        argument_model = WRITE_ACTION_ARGUMENT_MODELS[approval.action]
        try:
            parsed = argument_model.model_validate(decision.modified_arguments)
        except ValidationError as exc:
            details = [
                {
                    "location": list(error["loc"]),
                    "message": error["msg"],
                    "type": error["type"],
                }
                for error in exc.errors()
            ]
            raise ApprovalArgumentsInvalidError(details) from exc
        return parsed.model_dump(mode="json")

    @staticmethod
    def _matches_decision(
        existing: ApprovalRequest,
        *,
        actor_user_id: UUID,
        decision: ApprovalDecision,
        modified_arguments: dict[str, object] | None,
    ) -> bool:
        existing_decision_status = (
            ApprovalStatus.APPROVED
            if existing.status
            in {
                ApprovalStatus.EXECUTING,
                ApprovalStatus.EXECUTED,
                ApprovalStatus.EXECUTION_FAILED,
            }
            else existing.status
        )
        return (
            existing_decision_status is decision.status
            and existing.decided_by_user_id == actor_user_id
            and existing.modified_arguments == modified_arguments
            and existing.feedback == decision.feedback
        )
