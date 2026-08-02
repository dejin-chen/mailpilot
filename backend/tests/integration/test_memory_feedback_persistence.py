"""审批反馈更新长期记忆的合并、幂等、隔离和审计集成测试。"""

from uuid import uuid4

import pytest
from app.models.audit import AuditLog
from app.models.memory import MemoryProfileVersion, MemoryType
from app.schemas.memory import MemoryProfileCreate
from app.schemas.memory_feedback import (
    EmailStyleMemoryPatch,
    EmailStyleMemoryUpdateProposal,
)
from app.schemas.user import UserCreate
from app.services.memory import MemoryService
from app.services.user import UserService
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = pytest.mark.integration


async def test_feedback_patch_merges_old_value_and_is_idempotent(
    integration_session: AsyncSession,
) -> None:
    user = await UserService(integration_session).create_user(
        UserCreate(
            email=f"memory-feedback-{uuid4()}@example.com",
            password="memory-feedback-password",
            full_name="记忆反馈测试用户",
            timezone="Asia/Shanghai",
        )
    )
    service = MemoryService(integration_session)
    original = await service.create_profile(
        user_id=user.id,
        actor_user_id=user.id,
        data=MemoryProfileCreate(
            memory_type=MemoryType.EMAIL_STYLE,
            value={
                "tone": "正式",
                "signature": "张三｜研发部",
                "salutation": "您好",
            },
        ),
    )
    approval_request_id = uuid4()
    proposal = EmailStyleMemoryUpdateProposal(
        memory_type=MemoryType.EMAIL_STYLE,
        patch=EmailStyleMemoryPatch(tone="简洁专业"),
        evidence="以后邮件都使用简洁专业的语气",
        reason="用户明确表达长期风格偏好",
    )

    first = await service.apply_feedback_patch(
        user_id=user.id,
        actor_user_id=user.id,
        approval_request_id=approval_request_id,
        proposal=proposal,
        request_id="memory-feedback-001",
    )
    repeated = await service.apply_feedback_patch(
        user_id=user.id,
        actor_user_id=user.id,
        approval_request_id=approval_request_id,
        proposal=proposal,
        request_id="memory-feedback-001-repeated",
    )
    current = await service.get_profile(
        user_id=user.id,
        actor_user_id=user.id,
        memory_id=original.id,
    )

    assert first.applied is True
    assert first.version == 2
    assert repeated.reused is True
    assert repeated.version == 2
    assert current.value == {
        "tone": "简洁专业",
        "signature": "张三｜研发部",
        "salutation": "您好",
    }
    version_count = await integration_session.scalar(
        select(func.count())
        .select_from(MemoryProfileVersion)
        .where(MemoryProfileVersion.memory_profile_id == original.id)
    )
    assert version_count == 2

    event = await integration_session.scalar(
        select(AuditLog).where(
            AuditLog.user_id == user.id,
            AuditLog.action == "memory.updated_from_feedback",
            AuditLog.resource_id == original.id,
        )
    )
    assert event is not None
    assert event.details["version"] == 2
    assert event.details["source_type"] == "approval_feedback"
    assert "value" not in event.details
    assert "简洁专业" not in str(event.details)


async def test_same_source_reference_is_isolated_by_user(
    integration_session: AsyncSession,
) -> None:
    first_user = await UserService(integration_session).create_user(
        UserCreate(
            email=f"memory-feedback-owner-{uuid4()}@example.com",
            password="memory-feedback-password",
            full_name="第一位用户",
            timezone="Asia/Shanghai",
        )
    )
    second_user = await UserService(integration_session).create_user(
        UserCreate(
            email=f"memory-feedback-other-{uuid4()}@example.com",
            password="memory-feedback-password",
            full_name="第二位用户",
            timezone="Asia/Shanghai",
        )
    )
    proposal = EmailStyleMemoryUpdateProposal(
        memory_type=MemoryType.EMAIL_STYLE,
        patch=EmailStyleMemoryPatch(tone="友好简洁"),
        evidence="以后默认使用友好简洁的语气",
        reason="明确长期风格",
    )
    approval_request_id = uuid4()
    service = MemoryService(integration_session)

    first = await service.apply_feedback_patch(
        user_id=first_user.id,
        actor_user_id=first_user.id,
        approval_request_id=approval_request_id,
        proposal=proposal,
    )
    second = await service.apply_feedback_patch(
        user_id=second_user.id,
        actor_user_id=second_user.id,
        approval_request_id=approval_request_id,
        proposal=proposal,
    )

    assert first.applied is True
    assert second.applied is True
    assert first.memory_id != second.memory_id
