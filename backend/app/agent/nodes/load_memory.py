"""从 LangGraph Store 加载当前邮件真正需要的长期记忆。"""

import asyncio
import logging
from typing import TypeVar
from uuid import UUID

from langgraph.runtime import Runtime
from langgraph.store.base import BaseStore

from app.agent.context import AgentRuntimeContext
from app.agent.nodes.common import NodeUpdate, failure_update
from app.agent.schemas import AgentRunStatus
from app.agent.state import MailAgentState
from app.memory.schemas import (
    AgentMemoryContext,
    AgentMemoryReference,
    StoredMemoryEntry,
)
from app.memory.store import memory_store_namespace
from app.models.memory import MemoryType
from app.schemas.memory import (
    CalendarPreferencesMemory,
    ContactMemory,
    EmailStyleMemory,
    MemoryValue,
)

logger = logging.getLogger(__name__)
MemoryValueT = TypeVar("MemoryValueT", bound=MemoryValue)


class LoadMemoryNode:
    """按可信 user_id 精确读取默认偏好和当前发件人联系人。"""

    name = "load_memory"

    async def __call__(
        self,
        state: MailAgentState,
        runtime: Runtime[AgentRuntimeContext],
    ) -> NodeUpdate:
        context = runtime.context
        store = runtime.store
        if context is None:
            return failure_update(
                node=self.name,
                code="AGENT_CONTEXT_MISSING",
                message="加载长期记忆缺少可信运行上下文",
            )
        if store is None:
            return failure_update(
                node=self.name,
                code="AGENT_MEMORY_STORE_MISSING",
                message="LangGraph 长期记忆 Store 未注入",
                retryable=True,
            )

        sender = state.get("sender", "").strip().lower()
        try:
            email_entry, calendar_entry, contact_entry = await asyncio.gather(
                self._get_entry(
                    store,
                    user_id=context.user_id,
                    memory_type=MemoryType.EMAIL_STYLE,
                    memory_key="default",
                ),
                self._get_entry(
                    store,
                    user_id=context.user_id,
                    memory_type=MemoryType.CALENDAR_PREFERENCES,
                    memory_key="default",
                ),
                self._get_entry(
                    store,
                    user_id=context.user_id,
                    memory_type=MemoryType.CONTACT,
                    memory_key=sender,
                )
                if sender
                else self._empty_entry(),
            )
        except Exception as exc:
            logger.exception(
                "读取 LangGraph 长期记忆失败",
                extra={
                    "node": self.name,
                    "user_id": str(context.user_id),
                    "error_type": type(exc).__name__,
                },
            )
            return failure_update(
                node=self.name,
                code="MEMORY_STORE_READ_ERROR",
                message="读取长期记忆失败",
                retryable=True,
            )

        try:
            memory_context = AgentMemoryContext(
                email_style=self._parse_value(
                    email_entry,
                    MemoryType.EMAIL_STYLE,
                    EmailStyleMemory,
                ),
                calendar_preferences=self._parse_value(
                    calendar_entry,
                    MemoryType.CALENDAR_PREFERENCES,
                    CalendarPreferencesMemory,
                ),
                relevant_contact=self._parse_value(
                    contact_entry,
                    MemoryType.CONTACT,
                    ContactMemory,
                ),
                references=[
                    self._reference(entry)
                    for entry in (email_entry, calendar_entry, contact_entry)
                    if entry is not None
                ],
            )
        except Exception as exc:
            logger.warning(
                "LangGraph Store 中的长期记忆结构无效",
                extra={
                    "node": self.name,
                    "user_id": str(context.user_id),
                    "error_type": type(exc).__name__,
                },
            )
            return failure_update(
                node=self.name,
                code="MEMORY_STORE_DATA_INVALID",
                message="长期记忆副本结构无效",
            )

        return {
            "memory_context": memory_context,
            "current_node": self.name,
            "run_status": AgentRunStatus.RUNNING,
        }

    @staticmethod
    async def _get_entry(
        store: BaseStore,
        *,
        user_id: UUID,
        memory_type: MemoryType,
        memory_key: str,
    ) -> StoredMemoryEntry | None:
        namespace = memory_store_namespace(
            user_id=user_id,
            memory_type=memory_type,
        )
        item = await store.aget(namespace, memory_key)
        if item is None:
            return None
        entry = StoredMemoryEntry.model_validate(item.value)
        if entry.memory_type is not memory_type or entry.memory_key != memory_key:
            msg = "Store 记忆的类型或 key 与查询位置不一致"
            raise ValueError(msg)
        return entry

    @staticmethod
    async def _empty_entry() -> None:
        return None

    @staticmethod
    def _parse_value(
        entry: StoredMemoryEntry | None,
        expected_type: MemoryType,
        schema: type[MemoryValueT],
    ) -> MemoryValueT | None:
        if entry is None:
            return None
        if entry.memory_type is not expected_type:
            msg = "Store 记忆类型不匹配"
            raise ValueError(msg)
        return schema.model_validate(entry.value)

    @staticmethod
    def _reference(entry: StoredMemoryEntry) -> AgentMemoryReference:
        return AgentMemoryReference(
            memory_id=entry.memory_id,
            memory_type=entry.memory_type,
            memory_key=entry.memory_key,
            version=entry.version,
        )
