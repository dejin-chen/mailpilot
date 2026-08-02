"""长期记忆版本、审计和事务业务单元测试。"""

from datetime import UTC, datetime
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from app.models.memory import MemoryProfile, MemorySourceType, MemoryType
from app.repositories.audit import AuditRepository
from app.repositories.memory import (
    MemoryRecord,
    MemoryRepository,
    MemoryVersionRecord,
)
from app.schemas.memory import MemoryProfileCreate, MemoryProfileUpdate
from app.services.exceptions import (
    MemoryProfileNotFoundError,
    MemoryProfileVersionConflictError,
)
from app.services.memory import MemoryService
from sqlalchemy.ext.asyncio import AsyncSession

NOW = datetime(2026, 7, 24, 12, 0, tzinfo=UTC)


def _record(*, user_id: object, version: int = 1) -> MemoryRecord:
    return MemoryRecord(
        id=uuid4(),
        user_id=user_id,  # type: ignore[arg-type]
        memory_type=MemoryType.EMAIL_STYLE,
        memory_key="default",
        value={"tone": "简洁专业"},
        version=version,
        source_type=MemorySourceType.USER_API_EDIT,
        source_reference_type=None,
        source_reference_id=None,
        created_at=NOW,
        updated_at=NOW,
    )


@pytest.mark.asyncio
async def test_create_profile_adds_version_and_safe_audit_then_commits() -> None:
    session = AsyncMock(spec=AsyncSession)
    memories = AsyncMock(spec=MemoryRepository)
    audits = AsyncMock(spec=AuditRepository)
    user_id = uuid4()
    memory_id = uuid4()
    memories.get_by_logical_key.return_value = None

    async def assign_profile_fields(profile: MemoryProfile) -> MemoryProfile:
        profile.id = memory_id
        profile.created_at = NOW
        profile.updated_at = NOW
        return profile

    memories.add_profile.side_effect = assign_profile_fields
    memories.to_record.side_effect = MemoryRepository.to_record
    service = MemoryService(session, memories, audits)

    result = await service.create_profile(
        user_id=user_id,
        actor_user_id=user_id,
        data=MemoryProfileCreate(
            memory_type=MemoryType.EMAIL_STYLE,
            value={"tone": " 简洁专业 "},
        ),
        request_id="memory-create-001",
    )

    added_version = memories.add_version.await_args.args[0]
    audit = audits.add.await_args.args[0]
    assert result.id == memory_id
    assert result.value == {"tone": "简洁专业"}
    assert added_version.version == 1
    assert added_version.source_type is MemorySourceType.USER_API_EDIT
    assert audit.action == "memory.created"
    assert "value" not in audit.details
    assert "简洁专业" not in str(audit.details)
    session.commit.assert_awaited_once()
    session.rollback.assert_not_awaited()


@pytest.mark.asyncio
async def test_update_profile_appends_next_version_instead_of_overwriting() -> None:
    session = AsyncMock(spec=AsyncSession)
    memories = AsyncMock(spec=MemoryRepository)
    audits = AsyncMock(spec=AuditRepository)
    user_id = uuid4()
    profile = MemoryProfile(
        id=uuid4(),
        user_id=user_id,
        memory_type=MemoryType.EMAIL_STYLE,
        memory_key="default",
        current_version=1,
        created_at=NOW,
        updated_at=NOW,
    )
    memories.get_profile_for_update.return_value = profile
    memories.to_record.side_effect = MemoryRepository.to_record
    service = MemoryService(session, memories, audits)

    result = await service.update_profile(
        user_id=user_id,
        actor_user_id=user_id,
        memory_id=profile.id,
        data=MemoryProfileUpdate(
            memory_type=MemoryType.EMAIL_STYLE,
            expected_version=1,
            value={"tone": "友好简洁", "signature": "张三"},
        ),
    )

    added_version = memories.add_version.await_args.args[0]
    assert profile.current_version == 2
    assert added_version.version == 2
    assert added_version.value == {"tone": "友好简洁", "signature": "张三"}
    assert result.version == 2
    session.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_update_profile_rejects_stale_expected_version() -> None:
    session = AsyncMock(spec=AsyncSession)
    memories = AsyncMock(spec=MemoryRepository)
    audits = AsyncMock(spec=AuditRepository)
    user_id = uuid4()
    profile = MemoryProfile(
        id=uuid4(),
        user_id=user_id,
        memory_type=MemoryType.EMAIL_STYLE,
        memory_key="default",
        current_version=2,
    )
    memories.get_profile_for_update.return_value = profile
    service = MemoryService(session, memories, audits)

    with pytest.raises(MemoryProfileVersionConflictError):
        await service.update_profile(
            user_id=user_id,
            actor_user_id=user_id,
            memory_id=profile.id,
            data=MemoryProfileUpdate(
                memory_type=MemoryType.EMAIL_STYLE,
                expected_version=1,
                value={"tone": "旧页面提交"},
            ),
        )

    memories.add_version.assert_not_awaited()
    audits.add.assert_not_awaited()
    session.commit.assert_not_awaited()
    session.rollback.assert_awaited_once()


@pytest.mark.asyncio
async def test_get_profile_hides_missing_or_other_users_memory() -> None:
    session = AsyncMock(spec=AsyncSession)
    memories = AsyncMock(spec=MemoryRepository)
    audits = AsyncMock(spec=AuditRepository)
    memories.get_current.return_value = None
    service = MemoryService(session, memories, audits)

    with pytest.raises(MemoryProfileNotFoundError):
        await service.get_profile(
            user_id=uuid4(),
            actor_user_id=uuid4(),
            memory_id=uuid4(),
        )

    audits.add.assert_not_awaited()
    session.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_list_version_page_returns_history_and_writes_safe_audit() -> None:
    session = AsyncMock(spec=AsyncSession)
    memories = AsyncMock(spec=MemoryRepository)
    audits = AsyncMock(spec=AuditRepository)
    user_id = uuid4()
    current = _record(user_id=user_id, version=2)
    versions = [
        MemoryVersionRecord(
            id=uuid4(),
            memory_id=current.id,
            user_id=user_id,
            version=2,
            value={"tone": "友好简洁"},
            source_type=MemorySourceType.USER_API_EDIT,
            source_reference_type=None,
            source_reference_id=None,
            created_by_user_id=user_id,
            created_at=NOW,
        ),
        MemoryVersionRecord(
            id=uuid4(),
            memory_id=current.id,
            user_id=user_id,
            version=1,
            value={"tone": "正式"},
            source_type=MemorySourceType.USER_API_EDIT,
            source_reference_type=None,
            source_reference_id=None,
            created_by_user_id=user_id,
            created_at=NOW,
        ),
    ]
    memories.get_current.return_value = current
    memories.list_versions.return_value = versions
    memories.count_versions.return_value = 2

    result = await MemoryService(session, memories, audits).list_version_page(
        user_id=user_id,
        actor_user_id=user_id,
        memory_id=current.id,
        offset=0,
        limit=20,
        request_id="memory-version-list-001",
    )

    assert [item.version for item in result.items] == [2, 1]
    assert result.total == 2
    audit = audits.add.await_args.args[0]
    assert audit.action == "memory.versions_listed"
    assert audit.details["current_version"] == 2
    assert "value" not in audit.details
    assert "友好简洁" not in str(audit.details)
    session.commit.assert_awaited_once()
