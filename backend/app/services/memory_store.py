"""把长期记忆业务表的当前版本同步到 LangGraph Store。"""

import asyncio
import logging
from uuid import UUID

from langgraph.store.base import BaseStore
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.memory.schemas import StoredMemoryEntry
from app.memory.store import memory_store_namespace
from app.models.audit import AuditLog
from app.models.memory import MemoryType
from app.repositories.audit import AuditRepository
from app.repositories.memory import MemoryRecord, MemoryRepository
from app.services.exceptions import AgentMemoryStoreUnavailableError

logger = logging.getLogger(__name__)


class MemoryStoreSyncService:
    """以业务表为事实来源，建立可由不同 Graph thread 读取的 Store 副本。"""

    def __init__(
        self,
        session: AsyncSession,
        store: BaseStore,
        memory_repository: MemoryRepository | None = None,
        audit_repository: AuditRepository | None = None,
        timeout_seconds: float | None = None,
    ) -> None:
        self._session = session
        self._store = store
        self._memories = memory_repository or MemoryRepository(session)
        self._audits = audit_repository or AuditRepository(session)
        self._timeout_seconds = (
            timeout_seconds
            if timeout_seconds is not None
            else get_settings().agent_store_timeout_seconds
        )

    async def sync_user_memories(
        self,
        *,
        user_id: UUID,
        actor_user_id: UUID,
        agent_run_id: UUID,
        request_id: str | None = None,
    ) -> list[StoredMemoryEntry]:
        """同步当前版本、清理已删除副本，并记录本次 Agent 读取审计。"""

        try:
            async with asyncio.timeout(self._timeout_seconds):
                records = await self._memories.list_all_current(user_id=user_id)
                entries = [self._to_store_entry(record) for record in records]
                entries_by_type = {
                    memory_type: [
                        entry for entry in entries if entry.memory_type is memory_type
                    ]
                    for memory_type in MemoryType
                }
                for memory_type, typed_entries in entries_by_type.items():
                    await self._sync_namespace(
                        user_id=user_id,
                        memory_type=memory_type,
                        entries=typed_entries,
                    )

                await self._audits.add(
                    AuditLog(
                        user_id=user_id,
                        actor_user_id=actor_user_id,
                        agent_run_id=agent_run_id,
                        action="memory.loaded_for_agent",
                        resource_type="memory_profile_collection",
                        request_id=request_id,
                        details={
                            "memory_count": len(entries),
                            "references": [
                                {
                                    "memory_id": str(entry.memory_id),
                                    "memory_type": entry.memory_type.value,
                                    "memory_key": entry.memory_key,
                                    "version": entry.version,
                                }
                                for entry in entries
                            ],
                        },
                    )
                )
                await self._session.commit()
                return entries
        except AgentMemoryStoreUnavailableError:
            await self._session.rollback()
            raise
        except Exception as exc:
            await self._session.rollback()
            logger.exception(
                "同步用户长期记忆到 LangGraph Store 失败",
                extra={
                    "user_id": str(user_id),
                    "agent_run_id": str(agent_run_id),
                    "error_type": type(exc).__name__,
                },
            )
            raise AgentMemoryStoreUnavailableError from exc

    async def _sync_namespace(
        self,
        *,
        user_id: UUID,
        memory_type: MemoryType,
        entries: list[StoredMemoryEntry],
    ) -> None:
        namespace = memory_store_namespace(
            user_id=user_id,
            memory_type=memory_type,
        )
        existing_items = []
        offset = 0
        page_size = 200
        while True:
            page = await self._store.asearch(
                namespace,
                limit=page_size,
                offset=offset,
            )
            existing_items.extend(page)
            if len(page) < page_size:
                break
            offset += page_size
        existing_by_key = {item.key: item for item in existing_items}
        current_keys = {entry.memory_key for entry in entries}

        for stale_key in existing_by_key.keys() - current_keys:
            await self._store.adelete(namespace, stale_key)
        for entry in entries:
            payload = entry.model_dump(mode="json")
            existing = existing_by_key.get(entry.memory_key)
            if existing is not None and existing.value == payload:
                continue
            await self._store.aput(
                namespace,
                entry.memory_key,
                payload,
                index=False,
            )

    @staticmethod
    def _to_store_entry(record: MemoryRecord) -> StoredMemoryEntry:
        return StoredMemoryEntry(
            memory_id=record.id,
            memory_type=record.memory_type,
            memory_key=record.memory_key,
            version=record.version,
            value=record.value,
            updated_at=record.updated_at,
        )
