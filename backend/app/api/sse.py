"""Agent 执行事件的 SSE 编码和轮询生成器。"""

import asyncio
import json
from collections.abc import AsyncIterator
from time import monotonic
from uuid import UUID

from fastapi import Request

from app.models.agent_run import AgentRunStatus
from app.schemas.agent_run_event import AgentRunEventResponse
from app.services.agent_run import AgentRunService
from app.services.agent_run_event import AgentRunEventService
from app.services.mail_processing_workflow import SessionFactory

_STREAM_END_STATUSES = frozenset(
    {
        AgentRunStatus.WAITING_APPROVAL,
        AgentRunStatus.COMPLETED,
        AgentRunStatus.FAILED,
        AgentRunStatus.CANCELLED,
    }
)


async def stream_agent_run_events(
    *,
    request: Request,
    session_factory: SessionFactory,
    user_id: UUID,
    run_id: UUID,
    after: int,
    poll_interval_seconds: float = 0.5,
    heartbeat_seconds: float = 15.0,
) -> AsyncIterator[str]:
    """增量发送持久化事件，客户端断线后可凭最后 sequence 继续。"""

    cursor = after
    last_output_at = monotonic()
    while not await request.is_disconnected():
        async with session_factory() as session:
            events, has_more = await AgentRunEventService(session).list_events(
                user_id=user_id,
                agent_run_id=run_id,
                after=cursor,
                limit=100,
            )
            run = await AgentRunService(session).get_run(
                user_id=user_id,
                run_id=run_id,
            )

        for event in events:
            cursor = event.sequence
            yield encode_sse_event(
                event=event.event_type.value,
                data=AgentRunEventResponse.model_validate(event).model_dump(mode="json"),
                event_id=str(event.sequence),
            )
            last_output_at = monotonic()

        if has_more:
            continue
        if run.status in _STREAM_END_STATUSES:
            yield encode_sse_event(
                event="stream_closed",
                data={"status": run.status.value, "last_sequence": cursor},
            )
            return
        if monotonic() - last_output_at >= heartbeat_seconds:
            yield ": heartbeat\n\n"
            last_output_at = monotonic()
        await asyncio.sleep(poll_interval_seconds)


def encode_sse_event(
    *,
    event: str,
    data: object,
    event_id: str | None = None,
) -> str:
    """按照 SSE 文本协议编码单条事件，JSON 使用单行安全格式。"""

    lines: list[str] = []
    if event_id is not None:
        lines.append(f"id: {event_id}")
    lines.append(f"event: {event}")
    serialized = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    for line in serialized.splitlines() or [""]:
        lines.append(f"data: {line}")
    return "\n".join(lines) + "\n\n"
