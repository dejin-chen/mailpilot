"""AgentRun 数据访问。"""

from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.agent_run import AgentRun, AgentRunStatus


class AgentRunRepository:
    """所有 AgentRun 读取都显式限制当前用户。"""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_by_id(self, *, user_id: UUID, run_id: UUID) -> AgentRun | None:
        statement = select(AgentRun).where(
            AgentRun.id == run_id,
            AgentRun.user_id == user_id,
        )
        return await self._session.scalar(statement)

    async def get_for_update(self, *, user_id: UUID, run_id: UUID) -> AgentRun | None:
        """对状态迁移目标加行锁，避免并发请求同时修改。"""

        statement = (
            select(AgentRun)
            .where(AgentRun.id == run_id, AgentRun.user_id == user_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        return await self._session.scalar(statement)

    async def get_by_graph_thread_id(
        self,
        *,
        user_id: UUID,
        graph_thread_id: str,
    ) -> AgentRun | None:
        statement = select(AgentRun).where(
            AgentRun.user_id == user_id,
            AgentRun.graph_thread_id == graph_thread_id,
        )
        return await self._session.scalar(statement)

    async def list_runs(
        self,
        *,
        user_id: UUID,
        status: AgentRunStatus | None = None,
        offset: int = 0,
        limit: int = 20,
    ) -> list[AgentRun]:
        statement = select(AgentRun).where(AgentRun.user_id == user_id)
        if status is not None:
            statement = statement.where(AgentRun.status == status)
        statement = statement.order_by(AgentRun.created_at.desc(), AgentRun.id.desc())
        result = await self._session.scalars(statement.offset(offset).limit(limit))
        return list(result.all())

    async def count_runs(
        self,
        *,
        user_id: UUID,
        status: AgentRunStatus | None = None,
    ) -> int:
        statement = select(func.count()).select_from(AgentRun).where(AgentRun.user_id == user_id)
        if status is not None:
            statement = statement.where(AgentRun.status == status)
        return int(await self._session.scalar(statement) or 0)

    async def add(self, run: AgentRun) -> AgentRun:
        self._session.add(run)
        await self._session.flush()
        return run
