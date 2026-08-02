"""长期记忆的版本管理、用户隔离和审计业务。"""

from dataclasses import dataclass
from typing import Any
from uuid import UUID

from pydantic import JsonValue, ValidationError
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.audit import AuditLog
from app.models.memory import (
    MemoryProfile,
    MemoryProfileVersion,
    MemorySourceType,
    MemoryType,
)
from app.repositories.audit import AuditRepository
from app.repositories.memory import (
    MemoryRecord,
    MemoryRepository,
    MemoryVersionRecord,
)
from app.schemas.memory import (
    ContactMemory,
    MemoryProfileCreate,
    MemoryProfileUpdate,
    validate_memory_value,
)
from app.schemas.memory_feedback import (
    MemoryFeedbackUpdateResult,
    MemoryUpdateProposal,
)
from app.services.exceptions import (
    MemoryProfileConflictError,
    MemoryProfileNotFoundError,
    MemoryProfileValueInvalidError,
    MemoryProfileVersionConflictError,
)


@dataclass(frozen=True, slots=True)
class MemoryProfilePage:
    """长期记忆列表的 Service 分页结果。"""

    items: list[MemoryRecord]
    total: int
    offset: int
    limit: int


@dataclass(frozen=True, slots=True)
class MemoryVersionPage:
    """一项长期记忆的不可变历史版本分页结果。"""

    items: list[MemoryVersionRecord]
    total: int
    offset: int
    limit: int


class MemoryService:
    """统一执行类型校验、版本追加、用户隔离、事务和审计。"""

    def __init__(
        self,
        session: AsyncSession,
        memory_repository: MemoryRepository | None = None,
        audit_repository: AuditRepository | None = None,
    ) -> None:
        self._session = session
        self._memories = memory_repository or MemoryRepository(session)
        self._audits = audit_repository or AuditRepository(session)

    async def create_profile(
        self,
        *,
        user_id: UUID,
        actor_user_id: UUID,
        data: MemoryProfileCreate,
        source_type: MemorySourceType = MemorySourceType.USER_API_EDIT,
        source_reference_type: str | None = None,
        source_reference_id: UUID | None = None,
        request_id: str | None = None,
    ) -> MemoryRecord:
        """创建稳定档案及第 1 个不可变内容版本。"""

        value = self._canonicalize_value(
            memory_type=data.memory_type,
            memory_key=data.memory_key,
            value=data.value,
        )
        existing = await self._memories.get_by_logical_key(
            user_id=user_id,
            memory_type=data.memory_type,
            memory_key=data.memory_key,
        )
        if existing is not None:
            await self._session.rollback()
            raise MemoryProfileConflictError

        try:
            profile = MemoryProfile(
                user_id=user_id,
                memory_type=data.memory_type,
                memory_key=data.memory_key,
                current_version=1,
            )
            await self._memories.add_profile(profile)
            version = MemoryProfileVersion(
                memory_profile_id=profile.id,
                user_id=user_id,
                version=1,
                value=value,
                source_type=source_type,
                source_reference_type=source_reference_type,
                source_reference_id=source_reference_id,
                created_by_user_id=actor_user_id,
            )
            await self._memories.add_version(version)
            await self._add_audit(
                user_id=user_id,
                actor_user_id=actor_user_id,
                profile=profile,
                action="memory.created",
                source_type=source_type,
                request_id=request_id,
            )
            await self._session.commit()
            return self._memories.to_record(profile, version)
        except MemoryProfileConflictError:
            await self._session.rollback()
            raise
        except IntegrityError as exc:
            await self._session.rollback()
            raise MemoryProfileConflictError from exc
        except Exception:
            await self._session.rollback()
            raise

    async def update_profile(
        self,
        *,
        user_id: UUID,
        memory_id: UUID,
        actor_user_id: UUID,
        data: MemoryProfileUpdate,
        source_type: MemorySourceType = MemorySourceType.USER_API_EDIT,
        source_reference_type: str | None = None,
        source_reference_id: UUID | None = None,
        request_id: str | None = None,
    ) -> MemoryRecord:
        """锁定档案并追加新版本，旧内容不会被覆盖。"""

        try:
            profile = await self._memories.get_profile_for_update(
                user_id=user_id,
                memory_id=memory_id,
            )
            if profile is None:
                raise MemoryProfileNotFoundError
            if data.memory_type is not profile.memory_type:
                raise MemoryProfileValueInvalidError(
                    {"memory_type": "请求类型与现有记忆类型不一致"}
                )
            if data.expected_version != profile.current_version:
                raise MemoryProfileVersionConflictError(profile.current_version)

            value = self._canonicalize_value(
                memory_type=profile.memory_type,
                memory_key=profile.memory_key,
                value=data.value,
            )
            next_version = profile.current_version + 1
            profile.current_version = next_version
            version = MemoryProfileVersion(
                memory_profile_id=profile.id,
                user_id=user_id,
                version=next_version,
                value=value,
                source_type=source_type,
                source_reference_type=source_reference_type,
                source_reference_id=source_reference_id,
                created_by_user_id=actor_user_id,
            )
            await self._memories.add_version(version)
            # updated_at 使用数据库更新表达式；异步 ORM 必须在事务内显式刷新，
            # 避免提交后访问过期属性时发生隐式 I/O。
            await self._session.refresh(profile)
            await self._add_audit(
                user_id=user_id,
                actor_user_id=actor_user_id,
                profile=profile,
                action="memory.updated",
                source_type=source_type,
                request_id=request_id,
            )
            await self._session.commit()
            return self._memories.to_record(profile, version)
        except (
            MemoryProfileNotFoundError,
            MemoryProfileValueInvalidError,
            MemoryProfileVersionConflictError,
        ):
            await self._session.rollback()
            raise
        except IntegrityError as exc:
            await self._session.rollback()
            raise MemoryProfileVersionConflictError from exc
        except Exception:
            await self._session.rollback()
            raise

    async def get_profile(
        self,
        *,
        user_id: UUID,
        actor_user_id: UUID,
        memory_id: UUID,
        request_id: str | None = None,
    ) -> MemoryRecord:
        """读取当前版本，并留下不含记忆正文的审计事件。"""

        record = await self._memories.get_current(
            user_id=user_id,
            memory_id=memory_id,
        )
        if record is None:
            raise MemoryProfileNotFoundError
        await self._add_record_audit(
            user_id=user_id,
            actor_user_id=actor_user_id,
            record=record,
            action="memory.read",
            request_id=request_id,
        )
        await self._session.commit()
        return record

    async def list_profile_page(
        self,
        *,
        user_id: UUID,
        actor_user_id: UUID,
        memory_type: MemoryType | None = None,
        offset: int = 0,
        limit: int = 20,
        request_id: str | None = None,
    ) -> MemoryProfilePage:
        """分页读取当前用户记忆，并审计本次列表访问。"""

        items = await self._memories.list_current(
            user_id=user_id,
            memory_type=memory_type,
            offset=offset,
            limit=limit,
        )
        total = await self._memories.count_profiles(
            user_id=user_id,
            memory_type=memory_type,
        )
        await self._audits.add(
            AuditLog(
                user_id=user_id,
                actor_user_id=actor_user_id,
                action="memory.listed",
                resource_type="memory_profile",
                request_id=request_id,
                details={
                    "memory_type": memory_type.value if memory_type else None,
                    "returned_count": len(items),
                    "total": total,
                },
            )
        )
        await self._session.commit()
        return MemoryProfilePage(
            items=items,
            total=total,
            offset=offset,
            limit=limit,
        )

    async def delete_profile(
        self,
        *,
        user_id: UUID,
        actor_user_id: UUID,
        memory_id: UUID,
        request_id: str | None = None,
    ) -> None:
        """删除档案及内容版本，但保留不含正文的审计事件。"""

        try:
            profile = await self._memories.get_profile_for_update(
                user_id=user_id,
                memory_id=memory_id,
            )
            if profile is None:
                raise MemoryProfileNotFoundError
            await self._add_audit(
                user_id=user_id,
                actor_user_id=actor_user_id,
                profile=profile,
                action="memory.deleted",
                source_type=None,
                request_id=request_id,
            )
            await self._memories.delete_profile(profile)
            await self._session.commit()
        except MemoryProfileNotFoundError:
            await self._session.rollback()
            raise
        except Exception:
            await self._session.rollback()
            raise

    async def list_version_page(
        self,
        *,
        user_id: UUID,
        actor_user_id: UUID,
        memory_id: UUID,
        offset: int = 0,
        limit: int = 20,
        request_id: str | None = None,
    ) -> MemoryVersionPage:
        """读取当前用户一项记忆的版本历史，并记录最小审计信息。"""

        current = await self._memories.get_current(
            user_id=user_id,
            memory_id=memory_id,
        )
        if current is None:
            raise MemoryProfileNotFoundError

        items = await self._memories.list_versions(
            user_id=user_id,
            memory_id=memory_id,
            offset=offset,
            limit=limit,
        )
        total = await self._memories.count_versions(
            user_id=user_id,
            memory_id=memory_id,
        )
        await self._audits.add(
            AuditLog(
                user_id=user_id,
                actor_user_id=actor_user_id,
                action="memory.versions_listed",
                resource_type="memory_profile",
                resource_id=memory_id,
                request_id=request_id,
                details={
                    "memory_type": current.memory_type.value,
                    "memory_key": current.memory_key,
                    "current_version": current.version,
                    "returned_count": len(items),
                    "total": total,
                },
            )
        )
        await self._session.commit()
        return MemoryVersionPage(
            items=items,
            total=total,
            offset=offset,
            limit=limit,
        )

    async def apply_feedback_patch(
        self,
        *,
        user_id: UUID,
        actor_user_id: UUID,
        approval_request_id: UUID,
        proposal: MemoryUpdateProposal,
        request_id: str | None = None,
    ) -> MemoryFeedbackUpdateResult:
        """幂等合并一项审批反馈 Patch，并追加不可变记忆版本。"""

        source_type = MemorySourceType.APPROVAL_FEEDBACK
        source_reference_type = "approval_request"
        memory_key = str(proposal.memory_key).lower()
        existing_source = await self._memories.get_by_source_reference(
            user_id=user_id,
            source_type=source_type,
            source_reference_type=source_reference_type,
            source_reference_id=approval_request_id,
        )
        if existing_source is not None:
            return self._feedback_result(existing_source, reused=True)

        try:
            profile = await self._memories.get_profile_by_logical_key_for_update(
                user_id=user_id,
                memory_type=proposal.memory_type,
                memory_key=memory_key,
            )
            # 锁内再次检查，防止两个恢复请求同时通过锁外的快速检查。
            existing_source = await self._memories.get_by_source_reference(
                user_id=user_id,
                source_type=source_type,
                source_reference_type=source_reference_type,
                source_reference_id=approval_request_id,
            )
            if existing_source is not None:
                await self._session.rollback()
                return self._feedback_result(existing_source, reused=True)

            patch = proposal.patch.model_dump(mode="json", exclude_none=True)
            if profile is None:
                raw_value: dict[str, JsonValue] = dict(patch)
                if proposal.memory_type is MemoryType.CONTACT:
                    raw_value["email"] = memory_key
                value = self._canonicalize_value(
                    memory_type=proposal.memory_type,
                    memory_key=memory_key,
                    value=raw_value,
                )
                profile = MemoryProfile(
                    user_id=user_id,
                    memory_type=proposal.memory_type,
                    memory_key=memory_key,
                    current_version=1,
                )
                await self._memories.add_profile(profile)
                next_version = 1
                audit_action = "memory.created_from_feedback"
            else:
                current = await self._memories.get_version(
                    user_id=user_id,
                    memory_id=profile.id,
                    version=profile.current_version,
                )
                if current is None:
                    raise MemoryProfileNotFoundError
                raw_value = dict(current.value)
                raw_value.update(patch)
                value = self._canonicalize_value(
                    memory_type=profile.memory_type,
                    memory_key=profile.memory_key,
                    value=raw_value,
                )
                next_version = profile.current_version + 1
                profile.current_version = next_version
                audit_action = "memory.updated_from_feedback"

            version = MemoryProfileVersion(
                memory_profile_id=profile.id,
                user_id=user_id,
                version=next_version,
                value=value,
                source_type=source_type,
                source_reference_type=source_reference_type,
                source_reference_id=approval_request_id,
                created_by_user_id=actor_user_id,
            )
            await self._memories.add_version(version)
            await self._session.refresh(profile)
            await self._add_audit(
                user_id=user_id,
                actor_user_id=actor_user_id,
                profile=profile,
                action=audit_action,
                source_type=source_type,
                request_id=request_id,
            )
            await self._session.commit()
            record = self._memories.to_record(profile, version)
            return self._feedback_result(record, reused=False)
        except (
            MemoryProfileNotFoundError,
            MemoryProfileValueInvalidError,
        ):
            await self._session.rollback()
            raise
        except IntegrityError as exc:
            await self._session.rollback()
            # 数据库唯一约束是并发恢复的最后一道防线。
            existing_source = await self._memories.get_by_source_reference(
                user_id=user_id,
                source_type=source_type,
                source_reference_type=source_reference_type,
                source_reference_id=approval_request_id,
            )
            if existing_source is not None:
                return self._feedback_result(existing_source, reused=True)
            raise MemoryProfileConflictError from exc
        except Exception:
            await self._session.rollback()
            raise

    @staticmethod
    def _feedback_result(
        record: MemoryRecord,
        *,
        reused: bool,
    ) -> MemoryFeedbackUpdateResult:
        """把内部记忆记录转换为不暴露正文的 Graph 结果。"""

        return MemoryFeedbackUpdateResult(
            applied=not reused,
            reused=reused,
            memory_id=record.id,
            memory_type=record.memory_type,
            memory_key=record.memory_key,
            version=record.version,
        )

    @staticmethod
    def _canonicalize_value(
        *,
        memory_type: MemoryType,
        memory_key: str,
        value: dict[str, JsonValue],
    ) -> dict[str, Any]:
        """严格校验并转成可以稳定写入 JSONB 的普通 JSON 数据。"""

        try:
            typed_value = validate_memory_value(memory_type, value)
        except ValidationError as exc:
            raise MemoryProfileValueInvalidError(exc.errors()) from exc

        if memory_type in {
            MemoryType.EMAIL_STYLE,
            MemoryType.CALENDAR_PREFERENCES,
        }:
            if memory_key != "default":
                raise MemoryProfileValueInvalidError(
                    {"memory_key": "邮件风格和日历偏好只允许使用 default"}
                )
        elif memory_type is MemoryType.CONTACT:
            contact = ContactMemory.model_validate(typed_value)
            if memory_key != str(contact.email).lower():
                raise MemoryProfileValueInvalidError(
                    {"memory_key": "联系人记忆的 key 必须等于联系人邮箱"}
                )

        return typed_value.model_dump(mode="json", exclude_none=True)

    async def _add_audit(
        self,
        *,
        user_id: UUID,
        actor_user_id: UUID,
        profile: MemoryProfile,
        action: str,
        source_type: MemorySourceType | None,
        request_id: str | None,
    ) -> None:
        await self._audits.add(
            AuditLog(
                user_id=user_id,
                actor_user_id=actor_user_id,
                action=action,
                resource_type="memory_profile",
                resource_id=profile.id,
                request_id=request_id,
                details={
                    "memory_type": profile.memory_type.value,
                    "memory_key": profile.memory_key,
                    "version": profile.current_version,
                    "source_type": source_type.value if source_type else None,
                },
            )
        )

    async def _add_record_audit(
        self,
        *,
        user_id: UUID,
        actor_user_id: UUID,
        record: MemoryRecord,
        action: str,
        request_id: str | None,
    ) -> None:
        await self._audits.add(
            AuditLog(
                user_id=user_id,
                actor_user_id=actor_user_id,
                action=action,
                resource_type="memory_profile",
                resource_id=record.id,
                request_id=request_id,
                details={
                    "memory_type": record.memory_type.value,
                    "memory_key": record.memory_key,
                    "version": record.version,
                },
            )
        )
