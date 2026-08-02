"""不可变审计事件数据访问。"""

from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.audit import AuditLog


class AuditRepository:
    """只允许追加和按用户读取审计事件。"""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, event: AuditLog) -> AuditLog:
        self._session.add(event)
        await self._session.flush()
        return event

    async def list_events(
        self,
        *,
        user_id: UUID,
        offset: int = 0,
        limit: int = 100,
    ) -> list[AuditLog]:
        statement = (
            select(AuditLog)
            .where(AuditLog.user_id == user_id)
            .order_by(AuditLog.created_at.desc(), AuditLog.id.desc())
            .offset(offset)
            .limit(limit)
        )
        result = await self._session.scalars(statement)
        return list(result.all())

    async def list_page(
        self,
        *,
        user_id: UUID | None = None,
        action: str | None = None,
        offset: int = 0,
        limit: int = 100,
    ) -> tuple[list[AuditLog], int]:
        """按可选用户和动作筛选审计事件，并返回分页总数。"""

        conditions = []
        if user_id is not None:
            conditions.append(AuditLog.user_id == user_id)
        if action is not None:
            conditions.append(AuditLog.action == action)

        statement = (
            select(AuditLog)
            .where(*conditions)
            .order_by(AuditLog.created_at.desc(), AuditLog.id.desc())
            .offset(offset)
            .limit(limit)
        )
        count_statement = select(func.count()).select_from(AuditLog).where(*conditions)
        items = list((await self._session.scalars(statement)).all())
        total = int(await self._session.scalar(count_statement) or 0)
        return items, total
