"""MCP Server 对已审批写操作的授权、幂等和执行日志管理。"""

from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from pydantic import ValidationError
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.approval import ApprovalAction, ApprovalRequest, ApprovalStatus
from app.models.audit import AuditLog
from app.models.tool_call import ToolCallLog, ToolCallStatus
from app.repositories.approval import ApprovalRepository
from app.repositories.audit import AuditRepository
from app.repositories.tool_call import ToolCallLogRepository
from app.schemas.approval import WRITE_ACTION_ARGUMENT_MODELS
from app.services.exceptions import (
    ApprovalExecutionArgumentsMismatchError,
    ApprovalExecutionNotAllowedError,
    ApprovalRequestNotFoundError,
    ToolExecutionAlreadyFailedError,
    ToolExecutionInProgressError,
    ToolExecutionUncertainError,
)


@dataclass(frozen=True, slots=True)
class ToolExecutionPermit:
    """MCP 工具开始前获得的执行许可或可复用结果。"""

    approval: ApprovalRequest
    log: ToolCallLog
    reused: bool


def build_write_idempotency_key(
    *,
    approval_id: UUID,
    action: ApprovalAction,
    version: int,
) -> str:
    """由审批身份和版本生成稳定写操作幂等键。"""

    return f"write:{approval_id}:{action.value}:v{version}"


class ApprovedToolExecutionService:
    """只允许参数完全匹配的已审批操作开始一次写工具执行。"""

    def __init__(
        self,
        session: AsyncSession,
        approval_repository: ApprovalRepository | None = None,
        tool_call_repository: ToolCallLogRepository | None = None,
        audit_repository: AuditRepository | None = None,
    ) -> None:
        self._session = session
        self._approvals = approval_repository or ApprovalRepository(session)
        self._tool_calls = tool_call_repository or ToolCallLogRepository(session)
        self._audits = audit_repository or AuditRepository(session)

    async def begin(
        self,
        *,
        user_id: UUID,
        approval_id: UUID,
        action: ApprovalAction,
        arguments: dict[str, object],
        idempotency_key: str,
        request_id: str | None,
    ) -> ToolExecutionPermit:
        """校验审批和参数，持久化执行意图后才允许调用业务 Service。"""

        try:
            approval = await self._approvals.get_for_update(
                user_id=user_id,
                approval_id=approval_id,
            )
            if approval is None:
                raise ApprovalRequestNotFoundError
            normalized_arguments = self._validate_execution(
                approval=approval,
                action=action,
                arguments=arguments,
                idempotency_key=idempotency_key,
            )
            existing = await self._tool_calls.get_by_idempotency_key(
                user_id=user_id,
                idempotency_key=idempotency_key,
            )

            if approval.status is ApprovalStatus.EXECUTED:
                if (
                    existing is None
                    or existing.status is not ToolCallStatus.SUCCEEDED
                    or existing.result is None
                ):
                    raise ToolExecutionUncertainError
                await self._session.commit()
                return ToolExecutionPermit(approval=approval, log=existing, reused=True)

            if existing is not None:
                self._raise_for_existing(existing)

            if approval.status is ApprovalStatus.EXECUTING:
                raise ToolExecutionInProgressError
            if approval.status is ApprovalStatus.EXECUTION_FAILED:
                raise ToolExecutionAlreadyFailedError
            if approval.status is not ApprovalStatus.APPROVED:
                raise ApprovalExecutionNotAllowedError

            now = datetime.now(UTC)
            log = ToolCallLog(
                user_id=user_id,
                agent_run_id=approval.agent_run_id,
                approval_request_id=approval.id,
                tool_name=action.value,
                status=ToolCallStatus.RUNNING,
                arguments=normalized_arguments,
                idempotency_key=idempotency_key,
                request_id=request_id,
                attempt_count=1,
                started_at=now,
            )
            await self._tool_calls.add(log)
            approval.status = ApprovalStatus.EXECUTING
            await self._audits.add(
                AuditLog(
                    user_id=user_id,
                    agent_run_id=approval.agent_run_id,
                    approval_request_id=approval.id,
                    action="tool.execution_started",
                    resource_type="tool_call",
                    resource_id=log.id,
                    request_id=request_id,
                    details={
                        "tool_name": action.value,
                        "idempotency_key": idempotency_key,
                    },
                )
            )
            await self._session.commit()
            return ToolExecutionPermit(approval=approval, log=log, reused=False)
        except (
            ApprovalExecutionArgumentsMismatchError,
            ApprovalExecutionNotAllowedError,
            ApprovalRequestNotFoundError,
            ToolExecutionAlreadyFailedError,
            ToolExecutionInProgressError,
            ToolExecutionUncertainError,
        ):
            await self._session.rollback()
            raise
        except IntegrityError as exc:
            await self._session.rollback()
            existing = await self._tool_calls.get_by_idempotency_key(
                user_id=user_id,
                idempotency_key=idempotency_key,
            )
            if existing is not None:
                self._raise_for_existing(existing)
            raise ToolExecutionUncertainError from exc
        except Exception:
            await self._session.rollback()
            raise

    async def succeed(
        self,
        *,
        user_id: UUID,
        approval_id: UUID,
        idempotency_key: str,
        result: dict[str, object],
        request_id: str | None,
    ) -> ToolCallLog:
        """在同一事务中保存成功结果，并把审批状态推进到 executed。"""

        try:
            approval, log = await self._locked_execution(
                user_id=user_id,
                approval_id=approval_id,
                idempotency_key=idempotency_key,
            )
            if log.status is ToolCallStatus.SUCCEEDED:
                await self._session.commit()
                return log
            if log.status is not ToolCallStatus.RUNNING:
                raise ToolExecutionUncertainError

            now = datetime.now(UTC)
            log.status = ToolCallStatus.SUCCEEDED
            log.result = result
            log.completed_at = now
            log.latency_ms = max(0, int((now - log.started_at).total_seconds() * 1000))
            approval.status = ApprovalStatus.EXECUTED
            approval.executed_at = now
            approval.execution_result = result
            await self._audits.add(
                AuditLog(
                    user_id=user_id,
                    agent_run_id=approval.agent_run_id,
                    approval_request_id=approval.id,
                    action="tool.execution_succeeded",
                    resource_type="tool_call",
                    resource_id=log.id,
                    request_id=request_id,
                    details={
                        "tool_name": log.tool_name,
                        "latency_ms": log.latency_ms,
                        "reused_business_result": bool(result.get("reused")),
                    },
                )
            )
            await self._session.commit()
            return log
        except Exception:
            await self._session.rollback()
            raise

    async def fail(
        self,
        *,
        user_id: UUID,
        approval_id: UUID,
        idempotency_key: str,
        error_code: str,
        error_message: str,
        request_id: str | None,
    ) -> ToolCallLog:
        """记录已知失败；不自动创建新尝试，也不隐藏原始执行记录。"""

        try:
            approval, log = await self._locked_execution(
                user_id=user_id,
                approval_id=approval_id,
                idempotency_key=idempotency_key,
            )
            if log.status is not ToolCallStatus.RUNNING:
                await self._session.commit()
                return log

            now = datetime.now(UTC)
            safe_code = error_code[:100] or "TOOL_EXECUTION_FAILED"
            safe_message = error_message[:1000] or "写工具执行失败"
            log.status = ToolCallStatus.FAILED
            log.error_code = safe_code
            log.error_message = safe_message
            log.completed_at = now
            log.latency_ms = max(0, int((now - log.started_at).total_seconds() * 1000))
            approval.status = ApprovalStatus.EXECUTION_FAILED
            approval.executed_at = now
            approval.execution_result = {
                "success": False,
                "error_code": safe_code,
                "error_message": safe_message,
            }
            await self._audits.add(
                AuditLog(
                    user_id=user_id,
                    agent_run_id=approval.agent_run_id,
                    approval_request_id=approval.id,
                    action="tool.execution_failed",
                    resource_type="tool_call",
                    resource_id=log.id,
                    request_id=request_id,
                    details={
                        "tool_name": log.tool_name,
                        "error_code": safe_code,
                        "latency_ms": log.latency_ms,
                    },
                )
            )
            await self._session.commit()
            return log
        except Exception:
            await self._session.rollback()
            raise

    async def mark_uncertain(
        self,
        *,
        user_id: UUID,
        approval_id: UUID,
        idempotency_key: str,
        error_code: str,
        error_message: str,
        request_id: str | None,
    ) -> ToolCallLog:
        """传输中断时标记结果未知，保留 executing 审批并禁止自动重试。"""

        try:
            approval, log = await self._locked_execution(
                user_id=user_id,
                approval_id=approval_id,
                idempotency_key=idempotency_key,
            )
            if log.status is not ToolCallStatus.RUNNING:
                await self._session.commit()
                return log

            now = datetime.now(UTC)
            safe_code = error_code[:100] or "TOOL_EXECUTION_UNCERTAIN"
            safe_message = error_message[:1000] or "写工具执行结果暂时无法确定"
            log.status = ToolCallStatus.UNCERTAIN
            log.error_code = safe_code
            log.error_message = safe_message
            log.completed_at = now
            log.latency_ms = max(0, int((now - log.started_at).total_seconds() * 1000))
            approval.execution_result = {
                "success": False,
                "uncertain": True,
                "error_code": safe_code,
                "error_message": safe_message,
            }
            await self._audits.add(
                AuditLog(
                    user_id=user_id,
                    agent_run_id=approval.agent_run_id,
                    approval_request_id=approval.id,
                    action="tool.execution_uncertain",
                    resource_type="tool_call",
                    resource_id=log.id,
                    request_id=request_id,
                    details={
                        "tool_name": log.tool_name,
                        "error_code": safe_code,
                        "latency_ms": log.latency_ms,
                    },
                )
            )
            await self._session.commit()
            return log
        except Exception:
            await self._session.rollback()
            raise

    @staticmethod
    def _validate_execution(
        *,
        approval: ApprovalRequest,
        action: ApprovalAction,
        arguments: dict[str, object],
        idempotency_key: str,
    ) -> dict[str, object]:
        if approval.action is not action:
            raise ApprovalExecutionArgumentsMismatchError
        expected_key = build_write_idempotency_key(
            approval_id=approval.id,
            action=approval.action,
            version=approval.version,
        )
        if idempotency_key != expected_key:
            raise ApprovalExecutionArgumentsMismatchError

        model = WRITE_ACTION_ARGUMENT_MODELS[action]
        try:
            received = model.model_validate(arguments).model_dump(mode="json")
            approved = model.model_validate(
                approval.modified_arguments or approval.proposed_arguments
            ).model_dump(mode="json")
        except ValidationError as exc:
            raise ApprovalExecutionArgumentsMismatchError from exc
        if received != approved:
            raise ApprovalExecutionArgumentsMismatchError
        return received

    @staticmethod
    def _raise_for_existing(existing: ToolCallLog) -> None:
        if existing.status is ToolCallStatus.RUNNING:
            raise ToolExecutionInProgressError
        if existing.status is ToolCallStatus.FAILED:
            raise ToolExecutionAlreadyFailedError
        if existing.status is ToolCallStatus.UNCERTAIN:
            raise ToolExecutionUncertainError
        if existing.status is ToolCallStatus.SUCCEEDED:
            raise ToolExecutionUncertainError

    async def _locked_execution(
        self,
        *,
        user_id: UUID,
        approval_id: UUID,
        idempotency_key: str,
    ) -> tuple[ApprovalRequest, ToolCallLog]:
        approval = await self._approvals.get_for_update(
            user_id=user_id,
            approval_id=approval_id,
        )
        log = await self._tool_calls.get_for_update(
            user_id=user_id,
            idempotency_key=idempotency_key,
        )
        if approval is None:
            raise ApprovalRequestNotFoundError
        if log is None or log.approval_request_id != approval.id:
            raise ToolExecutionUncertainError
        return approval, log
