"""Agent 执行时间线写入、增量读取和安全摘要生成。"""

from collections.abc import Mapping
from enum import Enum
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.schemas import EmailClassification, ModelUsage, ToolExecutionResult
from app.models.agent_run_event import AgentRunEvent, AgentRunEventType
from app.repositories.agent_run import AgentRunRepository
from app.repositories.agent_run_event import AgentRunEventRepository
from app.schemas.agent_run_event import AgentRunEventPayload
from app.services.exceptions import AgentRunNotFoundError


class AgentRunEventService:
    """保证事件属于当前用户、序号连续并且 payload 经过白名单校验。"""

    def __init__(
        self,
        session: AsyncSession,
        event_repository: AgentRunEventRepository | None = None,
        run_repository: AgentRunRepository | None = None,
    ) -> None:
        self._session = session
        self._events = event_repository or AgentRunEventRepository(session)
        self._runs = run_repository or AgentRunRepository(session)

    async def append(
        self,
        *,
        user_id: UUID,
        agent_run_id: UUID,
        event_type: AgentRunEventType,
        payload: AgentRunEventPayload | None = None,
        node_name: str | None = None,
        commit: bool = True,
        run_locked: bool = False,
    ) -> AgentRunEvent:
        """追加事件；公开调用时先锁定 AgentRun，避免并发序号冲突。"""

        try:
            if not run_locked:
                run = await self._runs.get_for_update(
                    user_id=user_id,
                    run_id=agent_run_id,
                )
                if run is None:
                    raise AgentRunNotFoundError
            sequence = await self._events.next_sequence(agent_run_id=agent_run_id)
            event = AgentRunEvent(
                user_id=user_id,
                agent_run_id=agent_run_id,
                sequence=sequence,
                event_type=event_type,
                node_name=node_name,
                payload=(payload or AgentRunEventPayload()).model_dump(
                    mode="json",
                    exclude_none=True,
                ),
            )
            await self._events.add(event)
            if commit:
                await self._session.commit()
            return event
        except Exception:
            if commit:
                await self._session.rollback()
            raise

    async def list_events(
        self,
        *,
        user_id: UUID,
        agent_run_id: UUID,
        after: int = 0,
        limit: int = 100,
    ) -> tuple[list[AgentRunEvent], bool]:
        """先校验运行归属，再按 sequence 增量查询。"""

        run = await self._runs.get_by_id(user_id=user_id, run_id=agent_run_id)
        if run is None:
            raise AgentRunNotFoundError
        items = await self._events.list_after(
            user_id=user_id,
            agent_run_id=agent_run_id,
            after=after,
            limit=limit,
        )
        cursor = items[-1].sequence if items else after
        return items, await self._events.has_after(
            user_id=user_id,
            agent_run_id=agent_run_id,
            after=cursor,
        )


def build_node_event_payload(
    *,
    node_name: str,
    update: Mapping[str, Any] | None,
) -> AgentRunEventPayload:
    """只从节点更新中提取可公开摘要，拒绝复制邮件正文、Prompt 和工具完整结果。"""

    values = dict(update or {})
    payload: dict[str, Any] = {
        "current_node": node_name,
        "updated_fields": sorted(str(key) for key in values),
    }

    classification = values.get("classification")
    if classification is not None:
        try:
            item = EmailClassification.model_validate(classification)
            payload.update(
                action=item.action.value,
                priority=item.priority.value,
                category=item.category.value,
            )
        except Exception:
            pass

    tool_results = values.get("tool_results")
    if isinstance(tool_results, list):
        validated: list[ToolExecutionResult] = []
        for result in tool_results:
            try:
                validated.append(ToolExecutionResult.model_validate(result))
            except Exception:
                continue
        payload["tool_names"] = [item.tool_name.value for item in validated]
        payload["tool_success_count"] = sum(item.success for item in validated)
        payload["tool_failure_count"] = sum(not item.success for item in validated)

    approval_id = values.get("approval_request_id")
    if isinstance(approval_id, UUID):
        payload["approval_request_id"] = approval_id

    usages = values.get("model_usages")
    if isinstance(usages, list):
        validated_usages: list[ModelUsage] = []
        for usage in usages:
            try:
                validated_usages.append(ModelUsage.model_validate(usage))
            except Exception:
                continue
        payload["input_tokens"] = sum(item.input_tokens for item in validated_usages)
        payload["output_tokens"] = sum(item.output_tokens for item in validated_usages)
        payload["total_tokens"] = sum(item.total_tokens for item in validated_usages)

    error_code = values.get("error_code")
    if isinstance(error_code, str):
        payload["error_code"] = error_code
    return AgentRunEventPayload.model_validate(payload)


def event_value(value: object) -> str:
    """把状态枚举转换成稳定字符串，避免把任意对象写入 JSON。"""

    return str(value.value) if isinstance(value, Enum) else str(value)
