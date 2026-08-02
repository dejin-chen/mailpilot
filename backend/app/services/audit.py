"""管理员审计查询业务。"""

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.audit import AuditLog
from app.repositories.audit import AuditRepository


@dataclass(frozen=True, slots=True)
class AuditLogPage:
    """审计日志分页结果。"""

    items: list[AuditLog]
    total: int
    offset: int
    limit: int


class AuditService:
    """封装审计查询条件，写入仍由具体业务 Service 在事务内完成。"""

    def __init__(
        self,
        session: AsyncSession,
        repository: AuditRepository | None = None,
    ) -> None:
        self._repository = repository or AuditRepository(session)

    async def list_page_for_admin(
        self,
        *,
        user_id: UUID | None,
        action: str | None,
        offset: int,
        limit: int,
    ) -> AuditLogPage:
        """管理员可按数据所有者或动作筛选全系统审计事件。"""

        normalized_action = action.strip() if action is not None else None
        if normalized_action == "":
            normalized_action = None
        items, total = await self._repository.list_page(
            user_id=user_id,
            action=normalized_action,
            offset=offset,
            limit=limit,
        )
        return AuditLogPage(items=items, total=total, offset=offset, limit=limit)
