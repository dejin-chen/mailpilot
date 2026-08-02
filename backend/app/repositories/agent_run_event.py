"""Agent 执行事件数据访问。"""

from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.agent_run_event import AgentRunEvent


class AgentRunEventRepository:
    """按照用户、运行和递增序号读写执行时间线。"""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def next_sequence(self, *, agent_run_id: UUID) -> int:
        """在 AgentRun 行锁保护下计算下一序号。"""

        statement = select(func.coalesce(func.max(AgentRunEvent.sequence), 0) + 1).where(
            AgentRunEvent.agent_run_id == agent_run_id
        )
        return int(await self._session.scalar(statement) or 1)

    async def add(self, event: AgentRunEvent) -> AgentRunEvent:
        self._session.add(event)
        await self._session.flush()
        return event

    async def list_after(
        self,
        *,
        user_id: UUID,
        agent_run_id: UUID,
        after: int,
        limit: int,
    ) -> list[AgentRunEvent]:
        statement = (
            select(AgentRunEvent)
            .where(
                AgentRunEvent.user_id == user_id,
                AgentRunEvent.agent_run_id == agent_run_id,
                AgentRunEvent.sequence > after,
            )
            .order_by(AgentRunEvent.sequence.asc())
            .limit(limit)
        )
        result = await self._session.scalars(statement)
        return list(result.all())

    async def has_after(
        self,
        *,
        user_id: UUID,
        agent_run_id: UUID,
        after: int,
    ) -> bool:
        statement = select(
            select(AgentRunEvent.id)
            .where(
                AgentRunEvent.user_id == user_id,
                AgentRunEvent.agent_run_id == agent_run_id,
                AgentRunEvent.sequence > after,
            )
            .exists()
        )
        return bool(await self._session.scalar(statement))
