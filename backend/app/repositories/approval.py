"""人工审批请求数据访问。"""

from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.approval import ApprovalRequest, ApprovalStatus


class ApprovalRepository:
    """审批读取始终包含 user_id，决定操作使用行锁。"""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_by_id(
        self,
        *,
        user_id: UUID,
        approval_id: UUID,
    ) -> ApprovalRequest | None:
        statement = select(ApprovalRequest).where(
            ApprovalRequest.id == approval_id,
            ApprovalRequest.user_id == user_id,
        )
        return await self._session.scalar(statement)

    async def get_by_id_for_admin(self, *, approval_id: UUID) -> ApprovalRequest | None:
        """管理员只读场景按主键查询，不绕过决定操作的 user_id 限制。"""

        return await self._session.get(ApprovalRequest, approval_id)

    async def get_for_update(
        self,
        *,
        user_id: UUID,
        approval_id: UUID,
    ) -> ApprovalRequest | None:
        statement = (
            select(ApprovalRequest)
            .where(
                ApprovalRequest.id == approval_id,
                ApprovalRequest.user_id == user_id,
            )
            .with_for_update()
        )
        return await self._session.scalar(statement)

    async def get_by_idempotency_key(
        self,
        *,
        user_id: UUID,
        idempotency_key: str,
    ) -> ApprovalRequest | None:
        statement = select(ApprovalRequest).where(
            ApprovalRequest.user_id == user_id,
            ApprovalRequest.idempotency_key == idempotency_key,
        )
        return await self._session.scalar(statement)

    async def list_requests(
        self,
        *,
        user_id: UUID,
        status: ApprovalStatus | None = None,
        offset: int = 0,
        limit: int = 20,
    ) -> list[ApprovalRequest]:
        statement = select(ApprovalRequest).where(ApprovalRequest.user_id == user_id)
        if status is not None:
            statement = statement.where(ApprovalRequest.status == status)
        statement = statement.order_by(
            ApprovalRequest.created_at.desc(),
            ApprovalRequest.id.desc(),
        )
        result = await self._session.scalars(statement.offset(offset).limit(limit))
        return list(result.all())

    async def list_requests_for_admin(
        self,
        *,
        status: ApprovalStatus | None = None,
        offset: int = 0,
        limit: int = 20,
    ) -> list[ApprovalRequest]:
        """管理员审批中心查看全部用户的审批记录。"""

        statement = select(ApprovalRequest)
        if status is not None:
            statement = statement.where(ApprovalRequest.status == status)
        statement = statement.order_by(
            ApprovalRequest.created_at.desc(),
            ApprovalRequest.id.desc(),
        )
        result = await self._session.scalars(statement.offset(offset).limit(limit))
        return list(result.all())

    async def count_requests(
        self,
        *,
        user_id: UUID,
        status: ApprovalStatus | None = None,
    ) -> int:
        statement = (
            select(func.count())
            .select_from(ApprovalRequest)
            .where(ApprovalRequest.user_id == user_id)
        )
        if status is not None:
            statement = statement.where(ApprovalRequest.status == status)
        return int(await self._session.scalar(statement) or 0)

    async def count_requests_for_admin(
        self,
        *,
        status: ApprovalStatus | None = None,
    ) -> int:
        """统计全系统审批数量，仅供通过 RBAC 的管理员接口调用。"""

        statement = select(func.count()).select_from(ApprovalRequest)
        if status is not None:
            statement = statement.where(ApprovalRequest.status == status)
        return int(await self._session.scalar(statement) or 0)

    async def add(self, approval: ApprovalRequest) -> ApprovalRequest:
        self._session.add(approval)
        await self._session.flush()
        return approval
