"""AgentRun 创建与响应 Schema。"""

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models.agent_run import AgentRunStatus, AgentWorkflowName


class AgentRunCreate(BaseModel):
    """启动一次 Agent 执行所需的业务字段。"""

    email_thread_id: UUID
    graph_thread_id: str = Field(min_length=1, max_length=255)
    workflow_name: AgentWorkflowName = AgentWorkflowName.APPROVAL_GATE_V1

    @field_validator("graph_thread_id")
    @classmethod
    def normalize_graph_thread_id(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            msg = "graph_thread_id 不能为空"
            raise ValueError(msg)
        return normalized


class AgentRunResponse(BaseModel):
    """Agent 执行状态公开响应。"""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    email_thread_id: UUID
    graph_thread_id: str
    workflow_name: AgentWorkflowName
    status: AgentRunStatus
    current_node: str
    result: dict[str, Any]
    error_code: str | None
    error_message: str | None
    input_tokens: int
    output_tokens: int
    total_tokens: int
    started_at: datetime | None
    completed_at: datetime | None
    created_at: datetime
    updated_at: datetime


class AgentRunStartResponse(BaseModel):
    """启动邮件处理后返回运行快照和当前待审批单。"""

    run: AgentRunResponse
    approval_request_id: UUID | None = None
