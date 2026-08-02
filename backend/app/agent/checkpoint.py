"""LangGraph PostgreSQL Checkpointer 的安全配置与连接生命周期。"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

from app.agent.approval_schemas import (
    ApprovalGateStatus,
    ApprovalInterruptPayload,
    ApprovalResumeSignal,
    CancelEventArguments,
    CreateEventArguments,
    RescheduleEventArguments,
    SendEmailArguments,
    WriteActionExecution,
    WriteActionProposal,
    WriteExecutionStatus,
)
from app.agent.schemas import (
    AgentError,
    AgentRunStatus,
    CheckAvailabilityArguments,
    DraftPurpose,
    EmailAction,
    EmailCategory,
    EmailClassification,
    EmailDraft,
    EmailPriority,
    ExecutionPlan,
    ExtractedIntent,
    FinalResult,
    FindAvailableSlotsArguments,
    GetEmailThreadArguments,
    MeetingIntent,
    ModelUsage,
    PlanAction,
    PlanStep,
    ReadOnlyToolName,
    SearchEmailsArguments,
    TaskIntent,
    ToolExecutionResult,
)
from app.core.config import Settings, get_settings
from app.core.database_urls import build_psycopg_database_url
from app.memory.schemas import (
    AgentMemoryContext,
    AgentMemoryReference,
    StoredMemoryEntry,
)
from app.models.approval import ApprovalAction, ApprovalStatus
from app.models.memory import MemoryType
from app.schemas.memory import (
    CalendarPreferencesMemory,
    ContactMemory,
    EmailStyleMemory,
    MeetingWindow,
)
from app.schemas.memory_feedback import (
    CalendarPreferencesMemoryPatch,
    CalendarPreferencesMemoryUpdateProposal,
    ContactMemoryPatch,
    ContactMemoryUpdateProposal,
    EmailStyleMemoryPatch,
    EmailStyleMemoryUpdateProposal,
    FeedbackMemoryDecision,
    MemoryFeedbackUpdateResult,
)

_CHECKPOINT_ALLOWED_TYPES = (
    AgentError,
    AgentMemoryContext,
    AgentMemoryReference,
    AgentRunStatus,
    ApprovalAction,
    ApprovalGateStatus,
    ApprovalInterruptPayload,
    ApprovalResumeSignal,
    ApprovalStatus,
    CalendarPreferencesMemory,
    CalendarPreferencesMemoryPatch,
    CalendarPreferencesMemoryUpdateProposal,
    CancelEventArguments,
    CheckAvailabilityArguments,
    ContactMemory,
    ContactMemoryPatch,
    ContactMemoryUpdateProposal,
    CreateEventArguments,
    DraftPurpose,
    EmailAction,
    EmailCategory,
    EmailClassification,
    EmailDraft,
    EmailPriority,
    EmailStyleMemory,
    EmailStyleMemoryPatch,
    EmailStyleMemoryUpdateProposal,
    ExecutionPlan,
    ExtractedIntent,
    FinalResult,
    FindAvailableSlotsArguments,
    FeedbackMemoryDecision,
    GetEmailThreadArguments,
    MeetingIntent,
    MeetingWindow,
    MemoryFeedbackUpdateResult,
    MemoryType,
    ModelUsage,
    PlanAction,
    PlanStep,
    ReadOnlyToolName,
    RescheduleEventArguments,
    SearchEmailsArguments,
    SendEmailArguments,
    TaskIntent,
    StoredMemoryEntry,
    ToolExecutionResult,
    WriteActionExecution,
    WriteActionProposal,
    WriteExecutionStatus,
)


def build_checkpoint_serializer() -> JsonPlusSerializer:
    """只允许反序列化 MailPilot 明确使用的 Pydantic 类型。"""

    return JsonPlusSerializer(allowed_msgpack_modules=_CHECKPOINT_ALLOWED_TYPES)


def build_checkpoint_database_url(database_url: str) -> str:
    """把 SQLAlchemy URL 转为 psycopg Checkpointer 使用的 PostgreSQL URL。"""

    return build_psycopg_database_url(database_url)


@asynccontextmanager
async def open_postgres_checkpointer(
    settings: Settings | None = None,
    *,
    setup: bool = False,
) -> AsyncIterator[AsyncPostgresSaver]:
    """打开可并发复用的 Checkpointer 连接池，并在退出时可靠关闭。"""

    current_settings = settings or get_settings()
    pool = AsyncConnectionPool(
        conninfo=build_checkpoint_database_url(current_settings.database_url),
        kwargs={
            "autocommit": True,
            "prepare_threshold": 0,
            "row_factory": dict_row,
        },
        min_size=1,
        max_size=max(1, current_settings.database_pool_size),
        open=False,
        name="mailpilot-langgraph-checkpoint",
    )
    await pool.open(wait=True)
    try:
        checkpointer = AsyncPostgresSaver(
            conn=pool,
            serde=build_checkpoint_serializer(),
        )
        if setup:
            await checkpointer.setup()
        yield checkpointer
    finally:
        await pool.close()
