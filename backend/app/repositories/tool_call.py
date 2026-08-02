"""MCP 工具调用日志数据访问。"""

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.tool_call import ToolCallLog


class ToolCallLogRepository:
    """通过用户和幂等键读取写工具执行记录。"""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_by_idempotency_key(
        self,
        *,
        user_id: UUID,
        idempotency_key: str,
    ) -> ToolCallLog | None:
        statement = select(ToolCallLog).where(
            ToolCallLog.user_id == user_id,
            ToolCallLog.idempotency_key == idempotency_key,
        )
        return await self._session.scalar(statement)

    async def get_for_update(
        self,
        *,
        user_id: UUID,
        idempotency_key: str,
    ) -> ToolCallLog | None:
        statement = (
            select(ToolCallLog)
            .where(
                ToolCallLog.user_id == user_id,
                ToolCallLog.idempotency_key == idempotency_key,
            )
            .with_for_update()
        )
        return await self._session.scalar(statement)

    async def list_by_agent_run(
        self,
        *,
        user_id: UUID,
        agent_run_id: UUID,
    ) -> list[ToolCallLog]:
        statement = (
            select(ToolCallLog)
            .where(
                ToolCallLog.user_id == user_id,
                ToolCallLog.agent_run_id == agent_run_id,
            )
            .order_by(ToolCallLog.created_at.asc(), ToolCallLog.id.asc())
        )
        result = await self._session.scalars(statement)
        return list(result.all())

    async def add(self, log: ToolCallLog) -> ToolCallLog:
        self._session.add(log)
        await self._session.flush()
        return log
