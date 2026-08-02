"""MCP 写工具共享的审批验证与执行状态包装器。"""

from collections.abc import Awaitable, Callable
from contextlib import AbstractAsyncContextManager
from typing import Protocol
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import AppException
from app.mcp.security import McpIdentity
from app.models.approval import ApprovalAction
from app.reliability.locks import RedisWriteLockManager, WriteLockManager
from app.services.approved_tool_execution import ApprovedToolExecutionService


class SessionFactory(Protocol):
    """写工具包装器使用的异步 Session 工厂合同。"""

    def __call__(self) -> AbstractAsyncContextManager[AsyncSession]: ...


WriteOperation = Callable[[AsyncSession], Awaitable[dict[str, object]]]


async def execute_approved_write(
    *,
    session_factory: SessionFactory,
    identity: McpIdentity,
    approval_id: UUID,
    action: ApprovalAction,
    arguments: dict[str, object],
    idempotency_key: str,
    operation: WriteOperation,
    lock_manager: WriteLockManager | None = None,
) -> dict[str, object]:
    """先获取分布式锁，再按“登记意图→业务写入→登记结果”执行。"""

    manager = lock_manager or RedisWriteLockManager()
    async with manager.hold(idempotency_key):
        return await _execute_locked_write(
            session_factory=session_factory,
            identity=identity,
            approval_id=approval_id,
            action=action,
            arguments=arguments,
            idempotency_key=idempotency_key,
            operation=operation,
        )


async def _execute_locked_write(
    *,
    session_factory: SessionFactory,
    identity: McpIdentity,
    approval_id: UUID,
    action: ApprovalAction,
    arguments: dict[str, object],
    idempotency_key: str,
    operation: WriteOperation,
) -> dict[str, object]:
    """在已获得 Redis 锁的前提下执行数据库幂等状态机。"""

    async with session_factory() as session:
        permit = await ApprovedToolExecutionService(session).begin(
            user_id=identity.user_id,
            approval_id=approval_id,
            action=action,
            arguments=arguments,
            idempotency_key=idempotency_key,
            request_id=identity.request_id,
        )
    if permit.reused:
        result = permit.log.result
        if result is None:
            msg = "已执行工具缺少可复用结果"
            raise RuntimeError(msg)
        reused_result = dict(result)
        reused_result["reused"] = True
        return reused_result

    try:
        async with session_factory() as session:
            result = await operation(session)
    except Exception as exc:
        code = exc.code if isinstance(exc, AppException) else "TOOL_EXECUTION_FAILED"
        message = exc.message if isinstance(exc, AppException) else "写工具执行失败"
        async with session_factory() as session:
            await ApprovedToolExecutionService(session).fail(
                user_id=identity.user_id,
                approval_id=approval_id,
                idempotency_key=idempotency_key,
                error_code=code,
                error_message=message,
                request_id=identity.request_id,
            )
        raise

    async with session_factory() as session:
        await ApprovedToolExecutionService(session).succeed(
            user_id=identity.user_id,
            approval_id=approval_id,
            idempotency_key=idempotency_key,
            result=result,
            request_id=identity.request_id,
        )
    return result
