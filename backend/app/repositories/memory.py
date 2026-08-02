"""用户长期记忆档案和版本的数据访问。"""

from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.memory import (
    MemoryProfile,
    MemoryProfileVersion,
    MemorySourceType,
    MemoryType,
)


@dataclass(frozen=True, slots=True)
class MemoryRecord:
    """把稳定档案信息与当前版本内容组合成 Service 可用结果。"""

    id: UUID
    user_id: UUID
    memory_type: MemoryType
    memory_key: str
    value: dict[str, Any]
    version: int
    source_type: MemorySourceType
    source_reference_type: str | None
    source_reference_id: UUID | None
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class MemoryVersionRecord:
    """一条属于指定用户和记忆档案的不可变历史版本。"""

    id: UUID
    memory_id: UUID
    user_id: UUID
    version: int
    value: dict[str, Any]
    source_type: MemorySourceType
    source_reference_type: str | None
    source_reference_id: UUID | None
    created_by_user_id: UUID | None
    created_at: datetime


class MemoryRepository:
    """所有长期记忆查询都强制限定 user_id，事务由 Service 管理。"""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_current(
        self,
        *,
        user_id: UUID,
        memory_id: UUID,
    ) -> MemoryRecord | None:
        """读取当前用户指定记忆的最新版本。"""

        statement = (
            select(MemoryProfile, MemoryProfileVersion)
            .join(
                MemoryProfileVersion,
                (MemoryProfileVersion.memory_profile_id == MemoryProfile.id)
                & (MemoryProfileVersion.user_id == MemoryProfile.user_id)
                & (MemoryProfileVersion.version == MemoryProfile.current_version),
            )
            .where(
                MemoryProfile.id == memory_id,
                MemoryProfile.user_id == user_id,
            )
        )
        row = (await self._session.execute(statement)).one_or_none()
        if row is None:
            return None
        return self.to_record(row[0], row[1])

    async def get_by_logical_key(
        self,
        *,
        user_id: UUID,
        memory_type: MemoryType,
        memory_key: str,
    ) -> MemoryRecord | None:
        """在当前用户范围内按“类型 + key”读取当前版本。"""

        statement = (
            select(MemoryProfile, MemoryProfileVersion)
            .join(
                MemoryProfileVersion,
                (MemoryProfileVersion.memory_profile_id == MemoryProfile.id)
                & (MemoryProfileVersion.user_id == MemoryProfile.user_id)
                & (MemoryProfileVersion.version == MemoryProfile.current_version),
            )
            .where(
                MemoryProfile.user_id == user_id,
                MemoryProfile.memory_type == memory_type,
                MemoryProfile.memory_key == memory_key,
            )
        )
        row = (await self._session.execute(statement)).one_or_none()
        if row is None:
            return None
        return self.to_record(row[0], row[1])

    async def get_profile_for_update(
        self,
        *,
        user_id: UUID,
        memory_id: UUID,
    ) -> MemoryProfile | None:
        """锁定当前用户的一项档案，串行处理并发修改和删除。"""

        statement = (
            select(MemoryProfile)
            .where(
                MemoryProfile.id == memory_id,
                MemoryProfile.user_id == user_id,
            )
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        return await self._session.scalar(statement)

    async def get_profile_by_logical_key_for_update(
        self,
        *,
        user_id: UUID,
        memory_type: MemoryType,
        memory_key: str,
    ) -> MemoryProfile | None:
        """按用户、类型和 key 锁定档案，供反馈 Patch 原子合并。"""

        statement = (
            select(MemoryProfile)
            .where(
                MemoryProfile.user_id == user_id,
                MemoryProfile.memory_type == memory_type,
                MemoryProfile.memory_key == memory_key,
            )
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        return await self._session.scalar(statement)

    async def get_version(
        self,
        *,
        user_id: UUID,
        memory_id: UUID,
        version: int,
    ) -> MemoryProfileVersion | None:
        """读取指定用户、档案和版本号对应的不可变版本。"""

        statement = select(MemoryProfileVersion).where(
            MemoryProfileVersion.user_id == user_id,
            MemoryProfileVersion.memory_profile_id == memory_id,
            MemoryProfileVersion.version == version,
        )
        return await self._session.scalar(statement)

    async def get_by_source_reference(
        self,
        *,
        user_id: UUID,
        source_type: MemorySourceType,
        source_reference_type: str,
        source_reference_id: UUID,
    ) -> MemoryRecord | None:
        """读取同一可信来源已生成的版本，用于工作流恢复幂等。"""

        statement = (
            select(MemoryProfile, MemoryProfileVersion)
            .join(
                MemoryProfileVersion,
                (MemoryProfileVersion.memory_profile_id == MemoryProfile.id)
                & (MemoryProfileVersion.user_id == MemoryProfile.user_id),
            )
            .where(
                MemoryProfile.user_id == user_id,
                MemoryProfileVersion.source_type == source_type,
                MemoryProfileVersion.source_reference_type == source_reference_type,
                MemoryProfileVersion.source_reference_id == source_reference_id,
            )
        )
        row = (await self._session.execute(statement)).one_or_none()
        if row is None:
            return None
        return self.to_record(row[0], row[1])

    async def list_current(
        self,
        *,
        user_id: UUID,
        memory_type: MemoryType | None = None,
        offset: int = 0,
        limit: int = 20,
    ) -> list[MemoryRecord]:
        """分页读取当前用户的最新记忆，可按类型过滤。"""

        statement = (
            select(MemoryProfile, MemoryProfileVersion)
            .join(
                MemoryProfileVersion,
                (MemoryProfileVersion.memory_profile_id == MemoryProfile.id)
                & (MemoryProfileVersion.user_id == MemoryProfile.user_id)
                & (MemoryProfileVersion.version == MemoryProfile.current_version),
            )
            .where(MemoryProfile.user_id == user_id)
        )
        if memory_type is not None:
            statement = statement.where(MemoryProfile.memory_type == memory_type)
        statement = (
            statement.order_by(
                MemoryProfile.updated_at.desc(),
                MemoryProfile.id.desc(),
            )
            .offset(offset)
            .limit(limit)
        )
        rows = (await self._session.execute(statement)).all()
        return [self.to_record(row[0], row[1]) for row in rows]

    async def list_versions(
        self,
        *,
        user_id: UUID,
        memory_id: UUID,
        offset: int = 0,
        limit: int = 20,
    ) -> list[MemoryVersionRecord]:
        """按版本号倒序读取当前用户一项记忆的历史。"""

        statement = (
            select(MemoryProfileVersion)
            .join(
                MemoryProfile,
                (MemoryProfile.id == MemoryProfileVersion.memory_profile_id)
                & (MemoryProfile.user_id == MemoryProfileVersion.user_id),
            )
            .where(
                MemoryProfile.id == memory_id,
                MemoryProfile.user_id == user_id,
            )
            .order_by(MemoryProfileVersion.version.desc())
            .offset(offset)
            .limit(limit)
        )
        versions = (await self._session.scalars(statement)).all()
        return [self.to_version_record(version) for version in versions]

    async def count_versions(
        self,
        *,
        user_id: UUID,
        memory_id: UUID,
    ) -> int:
        """统计当前用户一项记忆的历史版本数量。"""

        statement = (
            select(func.count())
            .select_from(MemoryProfileVersion)
            .join(
                MemoryProfile,
                (MemoryProfile.id == MemoryProfileVersion.memory_profile_id)
                & (MemoryProfile.user_id == MemoryProfileVersion.user_id),
            )
            .where(
                MemoryProfile.id == memory_id,
                MemoryProfile.user_id == user_id,
            )
        )
        return int(await self._session.scalar(statement) or 0)

    async def list_all_current(self, *, user_id: UUID) -> list[MemoryRecord]:
        """读取当前用户的全部最新记忆，供 Store 同步使用。"""

        statement = (
            select(MemoryProfile, MemoryProfileVersion)
            .join(
                MemoryProfileVersion,
                (MemoryProfileVersion.memory_profile_id == MemoryProfile.id)
                & (MemoryProfileVersion.user_id == MemoryProfile.user_id)
                & (MemoryProfileVersion.version == MemoryProfile.current_version),
            )
            .where(MemoryProfile.user_id == user_id)
            .order_by(
                MemoryProfile.memory_type,
                MemoryProfile.memory_key,
                MemoryProfile.id,
            )
        )
        rows = (await self._session.execute(statement)).all()
        return [self.to_record(row[0], row[1]) for row in rows]

    async def count_profiles(
        self,
        *,
        user_id: UUID,
        memory_type: MemoryType | None = None,
    ) -> int:
        """统计当前用户的记忆档案数。"""

        statement = (
            select(func.count()).select_from(MemoryProfile).where(MemoryProfile.user_id == user_id)
        )
        if memory_type is not None:
            statement = statement.where(MemoryProfile.memory_type == memory_type)
        return int(await self._session.scalar(statement) or 0)

    async def add_profile(self, profile: MemoryProfile) -> MemoryProfile:
        """新增稳定档案并 flush，以获得主键。"""

        self._session.add(profile)
        await self._session.flush()
        return profile

    async def add_version(
        self,
        version: MemoryProfileVersion,
    ) -> MemoryProfileVersion:
        """追加一条不可变版本。"""

        self._session.add(version)
        await self._session.flush()
        return version

    async def delete_profile(self, profile: MemoryProfile) -> None:
        """删除档案；数据库外键会级联删除其内容版本。"""

        await self._session.delete(profile)
        await self._session.flush()

    @staticmethod
    def to_record(
        profile: MemoryProfile,
        version: MemoryProfileVersion,
    ) -> MemoryRecord:
        """组合档案和版本，避免上层依赖 ORM Join 行结构。"""

        return MemoryRecord(
            id=profile.id,
            user_id=profile.user_id,
            memory_type=profile.memory_type,
            memory_key=profile.memory_key,
            value=dict(version.value),
            version=version.version,
            source_type=version.source_type,
            source_reference_type=version.source_reference_type,
            source_reference_id=version.source_reference_id,
            created_at=profile.created_at,
            updated_at=profile.updated_at,
        )

    @staticmethod
    def to_version_record(version: MemoryProfileVersion) -> MemoryVersionRecord:
        """把历史版本 ORM 转换为不依赖 Session 的只读结果。"""

        return MemoryVersionRecord(
            id=version.id,
            memory_id=version.memory_profile_id,
            user_id=version.user_id,
            version=version.version,
            value=dict(version.value),
            source_type=version.source_type,
            source_reference_type=version.source_reference_type,
            source_reference_id=version.source_reference_id,
            created_by_user_id=version.created_by_user_id,
            created_at=version.created_at,
        )
