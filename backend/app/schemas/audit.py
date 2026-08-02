"""审计事件内部创建与公开响应 Schema。"""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, JsonValue


class AuditEventCreate(BaseModel):
    """Service 在同一事务内追加一条不可变审计事件。"""

    actor_user_id: UUID | None = None
    agent_run_id: UUID | None = None
    approval_request_id: UUID | None = None
    action: str = Field(min_length=1, max_length=100)
    resource_type: str = Field(min_length=1, max_length=100)
    resource_id: UUID | None = None
    request_id: str | None = Field(default=None, max_length=255)
    details: dict[str, JsonValue] = Field(default_factory=dict)


class AuditLogResponse(BaseModel):
    """管理员或数据所有者可查看的审计事件。"""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    user_id: UUID
    actor_user_id: UUID | None
    agent_run_id: UUID | None
    approval_request_id: UUID | None
    action: str
    resource_type: str
    resource_id: UUID | None
    request_id: str | None
    details: dict[str, JsonValue]
    created_at: datetime
