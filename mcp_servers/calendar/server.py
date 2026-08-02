"""通过 FastMCP 暴露企业日历能力。"""

from datetime import datetime
from typing import Annotated
from uuid import UUID

from app.core.config import get_settings
from app.db.session import AsyncSessionFactory
from app.mcp.errors import raise_mcp_tool_error
from app.mcp.execution import execute_approved_write
from app.mcp.schemas import (
    AvailableSlot,
    AvailableSlotsResult,
    CalendarAvailabilityResult,
    CalendarWriteResult,
)
from app.mcp.security import get_mcp_identity
from app.mcp.server import build_mcp_http_app
from app.models.approval import ApprovalAction
from app.schemas.calendar import CalendarEventResponse
from app.services.calendar import CalendarService
from mcp.server.fastmcp import Context, FastMCP
from mcp.types import ToolAnnotations
from pydantic import EmailStr, Field

settings = get_settings()
mcp = FastMCP(
    "MailPilot Calendar MCP",
    instructions="只访问当前运行时用户的企业日历；所有外部写操作必须经过人工审批。",
    json_response=True,
    stateless_http=True,
    host="0.0.0.0",
    port=8002,
)

READ_ONLY = ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True)
EXTERNAL_WRITE = ToolAnnotations(readOnlyHint=False, destructiveHint=True, idempotentHint=True)


@mcp.tool(annotations=READ_ONLY, structured_output=True)
async def check_availability(
    start_at: datetime,
    end_at: datetime,
    ctx: Context,
    exclude_event_id: UUID | None = None,
) -> CalendarAvailabilityResult:
    """检查当前用户给定带时区时间范围是否可用，并返回冲突事件。"""

    identity = get_mcp_identity()
    try:
        await ctx.info("检查日历可用性")
        async with AsyncSessionFactory() as session:
            conflicts = await CalendarService(session).find_conflicts(
                user_id=identity.user_id,
                start_at=start_at,
                end_at=end_at,
                exclude_event_id=exclude_event_id,
            )
            return CalendarAvailabilityResult(
                available=not conflicts,
                conflicts=[CalendarEventResponse.model_validate(item) for item in conflicts],
            )
    except Exception as exc:
        raise_mcp_tool_error(exc, request_id=identity.request_id)


@mcp.tool(annotations=READ_ONLY, structured_output=True)
async def find_available_slots(
    window_start: datetime,
    window_end: datetime,
    duration_minutes: Annotated[int, Field(ge=1, le=480)],
    ctx: Context,
    step_minutes: Annotated[int, Field(ge=5, le=120)] = 30,
    limit: Annotated[int, Field(ge=1, le=20)] = 5,
) -> AvailableSlotsResult:
    """在给定窗口内按固定步长搜索不与当前用户日程冲突的候选时段。"""

    identity = get_mcp_identity()
    try:
        await ctx.info("搜索日历可用时段")
        async with AsyncSessionFactory() as session:
            slots = await CalendarService(session).find_available_slots(
                user_id=identity.user_id,
                window_start=window_start,
                window_end=window_end,
                duration_minutes=duration_minutes,
                step_minutes=step_minutes,
                limit=limit,
            )
            return AvailableSlotsResult(
                slots=[AvailableSlot(start_at=item.start_at, end_at=item.end_at) for item in slots],
                duration_minutes=duration_minutes,
            )
    except Exception as exc:
        raise_mcp_tool_error(exc, request_id=identity.request_id)


@mcp.tool(annotations=EXTERNAL_WRITE, structured_output=True)
async def create_event(
    title: Annotated[str, Field(min_length=1, max_length=500)],
    start_at: datetime,
    end_at: datetime,
    attendees: list[EmailStr],
    timezone: Annotated[str, Field(min_length=1, max_length=64)],
    idempotency_key: Annotated[str, Field(min_length=1, max_length=255)],
    ctx: Context,
    approval_id: UUID,
) -> CalendarWriteResult:
    """创建当前用户已人工审批的会议，相同幂等键复用原结果。"""

    identity = get_mcp_identity()
    try:
        await ctx.info(f"创建已审批会议：{title}")

        async def operation(session) -> dict[str, object]:
            created = await CalendarService(session).create_event(
                user_id=identity.user_id,
                title=title,
                start_at=start_at,
                end_at=end_at,
                attendees=[str(item) for item in attendees],
                timezone=timezone,
                idempotency_key=idempotency_key,
            )
            return CalendarWriteResult(
                event=CalendarEventResponse.model_validate(created.event),
                reused=created.reused,
                approval_id=approval_id,
                idempotency_key=idempotency_key,
            ).model_dump(mode="json")

        result = await execute_approved_write(
            session_factory=AsyncSessionFactory,
            identity=identity,
            approval_id=approval_id,
            action=ApprovalAction.CREATE_EVENT,
            arguments={
                "title": title,
                "start_at": start_at,
                "end_at": end_at,
                "attendees": attendees,
                "timezone": timezone,
            },
            idempotency_key=idempotency_key,
            operation=operation,
        )
        return CalendarWriteResult.model_validate(result)
    except Exception as exc:
        raise_mcp_tool_error(exc, request_id=identity.request_id)


@mcp.tool(annotations=EXTERNAL_WRITE, structured_output=True)
async def reschedule_event(
    event_id: UUID,
    new_start_at: datetime,
    new_end_at: datetime,
    idempotency_key: Annotated[str, Field(min_length=1, max_length=255)],
    ctx: Context,
    approval_id: UUID,
) -> CalendarWriteResult:
    """把当前用户已审批的会议调整到新时间。"""

    identity = get_mcp_identity()
    try:
        await ctx.info(f"修改已审批会议：{event_id}")

        async def operation(session) -> dict[str, object]:
            updated = await CalendarService(session).reschedule_event(
                user_id=identity.user_id,
                event_id=event_id,
                new_start_at=new_start_at,
                new_end_at=new_end_at,
            )
            return CalendarWriteResult(
                event=CalendarEventResponse.model_validate(updated.event),
                reused=updated.reused,
                approval_id=approval_id,
                idempotency_key=idempotency_key,
            ).model_dump(mode="json")

        result = await execute_approved_write(
            session_factory=AsyncSessionFactory,
            identity=identity,
            approval_id=approval_id,
            action=ApprovalAction.RESCHEDULE_EVENT,
            arguments={
                "event_id": event_id,
                "new_start_at": new_start_at,
                "new_end_at": new_end_at,
            },
            idempotency_key=idempotency_key,
            operation=operation,
        )
        return CalendarWriteResult.model_validate(result)
    except Exception as exc:
        raise_mcp_tool_error(exc, request_id=identity.request_id)


@mcp.tool(annotations=EXTERNAL_WRITE, structured_output=True)
async def cancel_event(
    event_id: UUID,
    idempotency_key: Annotated[str, Field(min_length=1, max_length=255)],
    ctx: Context,
    approval_id: UUID,
) -> CalendarWriteResult:
    """取消当前用户已经人工审批的会议。"""

    identity = get_mcp_identity()
    try:
        await ctx.info(f"取消已审批会议：{event_id}")

        async def operation(session) -> dict[str, object]:
            cancelled = await CalendarService(session).cancel_event(
                user_id=identity.user_id,
                event_id=event_id,
            )
            return CalendarWriteResult(
                event=CalendarEventResponse.model_validate(cancelled.event),
                reused=cancelled.reused,
                approval_id=approval_id,
                idempotency_key=idempotency_key,
            ).model_dump(mode="json")

        result = await execute_approved_write(
            session_factory=AsyncSessionFactory,
            identity=identity,
            approval_id=approval_id,
            action=ApprovalAction.CANCEL_EVENT,
            arguments={"event_id": event_id},
            idempotency_key=idempotency_key,
            operation=operation,
        )
        return CalendarWriteResult.model_validate(result)
    except Exception as exc:
        raise_mcp_tool_error(exc, request_id=identity.request_id)


app = build_mcp_http_app(mcp, settings=settings, service_name="calendar-mcp")
