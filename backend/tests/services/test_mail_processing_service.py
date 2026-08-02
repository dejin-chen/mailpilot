"""邮件工作流应用 Service 的后台失败状态测试。"""

from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from app.agent.schemas import AgentRunStatus as GraphRunStatus
from app.models.agent_run import AgentRunStatus
from app.services.exceptions import (
    AgentMemoryStoreUnavailableError,
    AgentWorkflowExecutionError,
)
from app.services.mail_processing_workflow import (
    MailProcessingWorkflowService,
    _persist_background_failure,
)
from sqlalchemy.ext.asyncio import AsyncSession


@pytest.mark.asyncio
async def test_graph_factory_error_is_persisted_as_failed(monkeypatch) -> None:
    """模型配置等 Graph 构建错误也必须结束 pending 状态。"""

    user_id = uuid4()
    run = SimpleNamespace(
        id=uuid4(),
        user_id=user_id,
        email_thread_id=uuid4(),
        graph_thread_id="mail-processing-factory-error",
        status=AgentRunStatus.PENDING,
    )
    transitions: list[AgentRunStatus] = []

    class FakeRunService:
        def __init__(self, session) -> None:
            del session

        async def get_run(self, **kwargs):
            del kwargs
            return run

        async def transition_status(self, **kwargs):
            target_status = kwargs["target_status"]
            transitions.append(target_status)
            run.status = target_status
            return run

    def broken_graph_factory():
        raise RuntimeError("missing model configuration")

    monkeypatch.setattr(
        "app.services.mail_processing_workflow.AgentRunService",
        FakeRunService,
    )
    service = MailProcessingWorkflowService(
        AsyncMock(spec=AsyncSession),
        broken_graph_factory,
        AsyncMock(),
    )

    with pytest.raises(AgentWorkflowExecutionError):
        await service.execute(
            user_id=user_id,
            user_timezone="Asia/Shanghai",
            run_id=run.id,
            request_id="factory-error-request",
        )

    assert transitions == [AgentRunStatus.RUNNING, AgentRunStatus.FAILED]


@pytest.mark.asyncio
async def test_failure_status_write_does_not_hide_original_memory_error(
    monkeypatch,
) -> None:
    """首次失败回写也出错时，后台兜底仍应拿到最初的 Store 异常。"""

    user_id = uuid4()
    run_id = uuid4()
    run = SimpleNamespace(
        id=run_id,
        user_id=user_id,
        email_thread_id=uuid4(),
        graph_thread_id="mail-processing-memory-error",
        status=AgentRunStatus.PENDING,
    )
    transitions: list[AgentRunStatus] = []

    class FakeRunService:
        def __init__(self, session) -> None:
            del session

        async def get_run(self, **kwargs):
            del kwargs
            return run

        async def transition_status(self, **kwargs):
            target_status = kwargs["target_status"]
            transitions.append(target_status)
            if target_status is AgentRunStatus.FAILED:
                raise RuntimeError("failure status write failed")
            run.status = target_status
            return run

    class BrokenMemorySync:
        def __init__(self, *args) -> None:
            del args

        async def sync_user_memories(self, **kwargs):
            del kwargs
            raise AgentMemoryStoreUnavailableError

    monkeypatch.setattr(
        "app.services.mail_processing_workflow.AgentRunService",
        FakeRunService,
    )
    monkeypatch.setattr(
        "app.services.mail_processing_workflow.MemoryStoreSyncService",
        BrokenMemorySync,
    )
    service = MailProcessingWorkflowService(
        AsyncMock(spec=AsyncSession),
        lambda: object(),
        AsyncMock(),
    )

    with pytest.raises(AgentMemoryStoreUnavailableError):
        await service.execute(
            user_id=user_id,
            user_timezone="Asia/Shanghai",
            run_id=run_id,
            request_id="memory-error-request",
        )

    assert transitions == [AgentRunStatus.RUNNING, AgentRunStatus.FAILED]


@pytest.mark.asyncio
async def test_background_failure_uses_fresh_session_to_end_running_state(
    monkeypatch,
) -> None:
    """主执行 Session 失效后，兜底 Session 必须把 running 改成 failed。"""

    user_id = uuid4()
    run_id = uuid4()
    run = SimpleNamespace(id=run_id, status=AgentRunStatus.RUNNING)
    transitions: list[dict[str, object]] = []

    class FakeSessionContext:
        async def __aenter__(self):
            return object()

        async def __aexit__(self, exc_type, exc, traceback):
            del exc_type, exc, traceback

    class FakeRunService:
        def __init__(self, session) -> None:
            del session

        async def get_run(self, **kwargs):
            del kwargs
            return run

        async def transition_status(self, **kwargs):
            transitions.append(kwargs)
            run.status = kwargs["target_status"]
            return run

    monkeypatch.setattr(
        "app.services.mail_processing_workflow.AgentRunService",
        FakeRunService,
    )

    await _persist_background_failure(
        session_factory=FakeSessionContext,
        user_id=user_id,
        run_id=run_id,
        request_id="background-recovery-request",
        failure=AgentMemoryStoreUnavailableError(),
    )

    assert run.status is AgentRunStatus.FAILED
    assert transitions[0]["error_code"] == "AGENT_MEMORY_STORE_UNAVAILABLE"


@pytest.mark.parametrize("raw_status", [GraphRunStatus.FAILED, "failed"])
def test_terminal_status_accepts_enum_and_checkpoint_string(raw_status: object) -> None:
    """Checkpointer 反序列化为字符串时也必须保留失败终态和错误原因。"""

    status, error_code, error_message = MailProcessingWorkflowService._terminal_status(
        {
            "run_status": raw_status,
            "errors": [
                {
                    "code": "LLM_STRUCTURED_OUTPUT_ERROR",
                    "message": "模型返回的结构化结果无效",
                }
            ],
        }
    )

    assert status is AgentRunStatus.FAILED
    assert error_code == "LLM_STRUCTURED_OUTPUT_ERROR"
    assert error_message == "模型返回的结构化结果无效"


@pytest.mark.parametrize("raw_status", [GraphRunStatus.IGNORED, "ignored"])
def test_terminal_status_maps_ignored_graph_result_to_completed_run(
    raw_status: object,
) -> None:
    """无需处理是正常业务结论，数据库运行记录应显示完成而不是失败。"""

    status, error_code, error_message = MailProcessingWorkflowService._terminal_status(
        {"run_status": raw_status}
    )

    assert status is AgentRunStatus.COMPLETED
    assert error_code is None
    assert error_message is None


@pytest.mark.parametrize("raw_status", [None, "running", "unknown"])
def test_terminal_status_never_marks_invalid_or_incomplete_state_completed(
    raw_status: object,
) -> None:
    """缺失或非终态状态必须显式失败，不能误导前端显示完成。"""

    status, error_code, _ = MailProcessingWorkflowService._terminal_status(
        {"run_status": raw_status}
    )

    assert status is AgentRunStatus.FAILED
    assert error_code in {
        "AGENT_WORKFLOW_STATUS_INVALID",
        "AGENT_WORKFLOW_INCOMPLETE",
    }
