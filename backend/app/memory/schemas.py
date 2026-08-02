"""写入 LangGraph Store 和当前 Graph State 的长期记忆结构。"""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, JsonValue

from app.models.memory import MemoryType
from app.schemas.memory import (
    CalendarPreferencesMemory,
    ContactMemory,
    EmailStyleMemory,
)


class StoredMemoryEntry(BaseModel):
    """Store 中一项可校验、可追溯到业务表版本的记忆副本。"""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = 1
    memory_id: UUID
    memory_type: MemoryType
    memory_key: str = Field(min_length=1, max_length=255)
    version: int = Field(ge=1)
    value: dict[str, JsonValue]
    updated_at: datetime


class AgentMemoryReference(BaseModel):
    """本次 Graph 实际加载的一项记忆版本引用。"""

    model_config = ConfigDict(extra="forbid")

    memory_id: UUID
    memory_type: MemoryType
    memory_key: str
    version: int = Field(ge=1)


class AgentMemoryContext(BaseModel):
    """一次邮件处理真正需要的最小长期记忆快照。"""

    model_config = ConfigDict(extra="forbid")

    email_style: EmailStyleMemory | None = None
    calendar_preferences: CalendarPreferencesMemory | None = None
    relevant_contact: ContactMemory | None = None
    references: list[AgentMemoryReference] = Field(default_factory=list)
