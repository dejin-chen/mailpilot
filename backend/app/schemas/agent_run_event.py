"""Agent 执行事件请求与响应 Schema。"""

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.models.agent_run_event import AgentRunEventType


class AgentRunEventPayload(BaseModel):
    """事件允许公开给页面的安全摘要字段。"""

    model_config = ConfigDict(extra="forbid")

    status: str | None = Field(default=None, max_length=50)
    previous_status: str | None = Field(default=None, max_length=50)
    current_node: str | None = Field(default=None, max_length=100)
    updated_fields: list[str] = Field(default_factory=list, max_length=50)
    action: str | None = Field(default=None, max_length=50)
    priority: str | None = Field(default=None, max_length=50)
    category: str | None = Field(default=None, max_length=50)
    tool_names: list[str] = Field(default_factory=list, max_length=20)
    tool_success_count: int | None = Field(default=None, ge=0)
    tool_failure_count: int | None = Field(default=None, ge=0)
    approval_request_id: UUID | None = None
    error_code: str | None = Field(default=None, max_length=100)
    message: str | None = Field(default=None, max_length=500)
    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)
    total_tokens: int | None = Field(default=None, ge=0)


class AgentRunEventResponse(BaseModel):
    """执行轨迹中的一条持久化事件。"""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    agent_run_id: UUID
    sequence: int
    event_type: AgentRunEventType
    node_name: str | None
    payload: dict[str, Any]
    created_at: datetime


class AgentRunEventListResponse(BaseModel):
    """支持按 sequence 增量读取的事件列表。"""

    items: list[AgentRunEventResponse]
    after: int
    next_after: int
    has_more: bool
