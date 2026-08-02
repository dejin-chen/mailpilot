"""真实 MCP Streamable HTTP、LangChain Adapter 与 PostgreSQL 集成测试。"""

import json
import os
from collections.abc import Callable
from datetime import UTC, datetime
from importlib import reload
from typing import Any
from uuid import UUID, uuid4

import httpx
import pytest
from app.models.agent_run import AgentRunStatus
from app.models.approval import ApprovalAction, ApprovalRequest, ApprovalStatus
from app.models.audit import AuditLog
from app.models.calendar import CalendarEvent, CalendarEventStatus
from app.models.email import EmailDirection, EmailMessage
from app.models.tool_call import ToolCallLog, ToolCallStatus
from app.models.user import User
from app.schemas.agent_run import AgentRunCreate
from app.schemas.approval import ApprovalDecision, ApprovalRequestCreate
from app.schemas.calendar import CalendarEventImport
from app.schemas.email import EmailDraftCreate, EmailMessageImport
from app.schemas.user import UserCreate
from app.services.agent_run import AgentRunService
from app.services.approval import ApprovalService
from app.services.approved_tool_execution import build_write_idempotency_key
from app.services.calendar import CalendarService
from app.services.email import EmailService
from app.services.user import UserService
from httpx import ASGITransport, AsyncClient
from langchain_mcp_adapters.client import MultiServerMCPClient
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from mcp_servers.calendar import server as calendar_server
from mcp_servers.mail import server as mail_server

pytestmark = pytest.mark.integration


def _http_factory(app) -> Callable[..., AsyncClient]:
    """让真实 MCP HTTP 协议在测试进程内通过 ASGITransport 传输。"""

    def factory(
        headers: dict[str, str] | None = None,
        timeout: httpx.Timeout | None = None,
        auth: httpx.Auth | None = None,
    ) -> AsyncClient:
        return AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://mcp-test",
            headers=headers,
            timeout=timeout,
            auth=auth,
            follow_redirects=True,
        )

    return factory


def _headers(user_id: UUID) -> dict[str, str]:
    return {
        "Authorization": "Bearer test-only-mcp-internal-token-at-least-32-chars",
        "X-MailPilot-User-ID": str(user_id),
        "X-Request-ID": "mcp-e2e-001",
    }


def _connections(user_id: UUID) -> dict[str, dict[str, Any]]:
    return {
        "mail": {
            "transport": "streamable_http",
            "url": "http://mcp-test/mcp",
            "headers": _headers(user_id),
            "httpx_client_factory": _http_factory(mail_server.app),
        },
        "calendar": {
            "transport": "streamable_http",
            "url": "http://mcp-test/mcp",
            "headers": _headers(user_id),
            "httpx_client_factory": _http_factory(calendar_server.app),
        },
    }


def _structured_result(result: object) -> dict[str, Any]:
    """从 LangChain 标准内容块中取出 MCP 工具的结构化 JSON。"""

    if isinstance(result, dict):
        return result
    if isinstance(result, str):
        parsed = json.loads(result)
        assert isinstance(parsed, dict)
        return parsed
    if isinstance(result, list):
        for block in result:
            if (
                isinstance(block, dict)
                and block.get("type") == "text"
                and isinstance(block.get("text"), str)
            ):
                parsed = json.loads(block["text"])
                assert isinstance(parsed, dict)
                return parsed
    raise AssertionError(f"无法解析 MCP 工具结果：{result!r}")


def _result_text(result: object) -> str:
    """错误结果可能不是结构化成功响应，保留文本检查辅助函数。"""

    return json.dumps(result, ensure_ascii=False, default=str)


async def test_langchain_adapter_calls_real_mcp_tools_and_database(monkeypatch) -> None:
    database_url = os.getenv("TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("未配置 TEST_DATABASE_URL，跳过 MCP Streamable HTTP 集成测试")

    engine = create_async_engine(database_url, poolclass=NullPool)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(mail_server, "AsyncSessionFactory", session_factory)
    monkeypatch.setattr(calendar_server, "AsyncSessionFactory", session_factory)

    owner_email = f"mcp-owner-{uuid4().hex}@example.com"
    stranger_email = f"mcp-stranger-{uuid4().hex}@example.com"
    async with session_factory() as session:
        owner = await UserService(session).create_user(
            UserCreate(
                email=owner_email,
                password="mcp-integration-password",
                full_name="MCP 所有者",
                timezone="Asia/Shanghai",
            )
        )
        stranger = await UserService(session).create_user(
            UserCreate(
                email=stranger_email,
                password="mcp-integration-password",
                full_name="MCP 其他用户",
                timezone="Asia/Shanghai",
            )
        )
        imported = await EmailService(session).import_inbound_email(
            user_id=owner.id,
            data=EmailMessageImport(
                provider="local",
                thread_external_id="mcp-thread-001",
                message_external_id="mcp-message-001",
                subject="MCP 协议会议",
                sender="manager@example.com",
                recipients=[owner_email],
                body_text="请使用 MCP 检查会议时间。",
                sent_at=datetime(2026, 7, 20, 2, 0, tzinfo=UTC),
            ),
        )
        await CalendarService(session).import_event(
            user_id=owner.id,
            data=CalendarEventImport(
                provider="local",
                external_id="mcp-event-001",
                title="MCP 已有会议",
                start_at=datetime(2026, 7, 20, 3, 0, tzinfo=UTC),
                end_at=datetime(2026, 7, 20, 4, 0, tzinfo=UTC),
                timezone="Asia/Shanghai",
                idempotency_key="mcp-event-seed-001",
            ),
        )
        owner_id = owner.id
        stranger_id = stranger.id
        thread_id = imported.thread.id

    try:
        async with mail_server.app.router.lifespan_context(mail_server.app):
            async with calendar_server.app.router.lifespan_context(calendar_server.app):
                client = MultiServerMCPClient(_connections(owner_id))
                tools = {tool.name: tool for tool in await client.get_tools()}
                assert set(tools) == {
                    "get_email_thread",
                    "search_emails",
                    "create_email_draft",
                    "mark_email_processed",
                    "send_email",
                    "check_availability",
                    "find_available_slots",
                    "create_event",
                    "reschedule_event",
                    "cancel_event",
                }

                search_result = await tools["search_emails"].ainvoke({"query": "MCP"})
                availability_result = await tools["check_availability"].ainvoke(
                    {
                        "start_at": "2026-07-20T03:30:00Z",
                        "end_at": "2026-07-20T03:45:00Z",
                    }
                )
                draft_arguments = {
                    "thread_id": str(thread_id),
                    "recipients": ["manager@example.com"],
                    "body_text": "已确认会议时间。",
                    "idempotency_key": "mcp-draft-e2e-001",
                }
                await tools["create_email_draft"].ainvoke(draft_arguments)
                reused_result = await tools["create_email_draft"].ainvoke(draft_arguments)
                blocked_result = await tools["send_email"].ainvoke(
                    {
                        "draft_message_id": str(uuid4()),
                        "approval_id": str(uuid4()),
                        "idempotency_key": "write-without-approval",
                    }
                )

                search_data = _structured_result(search_result)
                availability_data = _structured_result(availability_result)
                reused_data = _structured_result(reused_result)

                assert search_data["total"] == 1
                assert search_data["items"][0]["subject"] == "MCP 协议会议"
                assert availability_data["available"] is False
                assert reused_data["reused"] is True
                assert "APPROVAL_REQUEST_NOT_FOUND" in _result_text(blocked_result)

                stranger_client = MultiServerMCPClient(_connections(stranger_id))
                stranger_tools = {tool.name: tool for tool in await stranger_client.get_tools()}
                stranger_result = await stranger_tools["search_emails"].ainvoke({"query": "MCP"})
                assert _structured_result(stranger_result)["total"] == 0

        async with session_factory() as session:
            draft_count = await session.scalar(
                select(func.count())
                .select_from(EmailMessage)
                .where(
                    EmailMessage.user_id == owner_id,
                    EmailMessage.direction == EmailDirection.DRAFT,
                )
            )
            assert draft_count == 1
    finally:
        async with session_factory() as session:
            await session.execute(delete(User).where(User.id.in_([owner_id, stranger_id])))
            await session.commit()
        await engine.dispose()


async def test_approved_calendar_writes_execute_through_real_mcp(monkeypatch) -> None:
    """创建、改期和取消都必须经审批校验，并真实写入 PostgreSQL。"""

    database_url = os.getenv("TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("未配置 TEST_DATABASE_URL，跳过日历 MCP 写操作测试")

    engine = create_async_engine(database_url, poolclass=NullPool)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    write_calendar_server = reload(calendar_server)
    monkeypatch.setattr(
        write_calendar_server,
        "AsyncSessionFactory",
        session_factory,
    )
    owner_id: UUID | None = None

    try:
        async with session_factory() as session:
            email = f"calendar-write-{uuid4().hex}@example.com"
            owner = await UserService(session).create_user(
                UserCreate(
                    email=email,
                    password="calendar-write-password",
                    full_name="日历写操作用户",
                    timezone="Asia/Shanghai",
                )
            )
            imported = await EmailService(session).import_inbound_email(
                user_id=owner.id,
                data=EmailMessageImport(
                    provider="local",
                    thread_external_id=f"calendar-write-thread-{uuid4()}",
                    message_external_id=f"calendar-write-message-{uuid4()}",
                    subject="安排多个测试会议",
                    sender="manager@example.com",
                    recipients=[email],
                    body_text="请安排、修改并取消测试日程。",
                    sent_at=datetime(2026, 7, 24, 8, 0, tzinfo=UTC),
                ),
            )
            reschedule_seed = await CalendarService(session).import_event(
                user_id=owner.id,
                data=CalendarEventImport(
                    provider="local",
                    external_id=f"reschedule-seed-{uuid4()}",
                    title="待改期会议",
                    start_at=datetime(2026, 8, 1, 10, 0, tzinfo=UTC),
                    end_at=datetime(2026, 8, 1, 11, 0, tzinfo=UTC),
                    timezone="Asia/Shanghai",
                    idempotency_key=f"seed-{uuid4()}",
                ),
            )
            cancel_seed = await CalendarService(session).import_event(
                user_id=owner.id,
                data=CalendarEventImport(
                    provider="local",
                    external_id=f"cancel-seed-{uuid4()}",
                    title="待取消会议",
                    start_at=datetime(2026, 8, 1, 14, 0, tzinfo=UTC),
                    end_at=datetime(2026, 8, 1, 15, 0, tzinfo=UTC),
                    timezone="Asia/Shanghai",
                    idempotency_key=f"seed-{uuid4()}",
                ),
            )

            async def create_approved_action(
                action: ApprovalAction,
                arguments: dict[str, object],
            ) -> tuple[UUID, str]:
                run = await AgentRunService(session).create_run(
                    user_id=owner.id,
                    data=AgentRunCreate(
                        email_thread_id=imported.thread.id,
                        graph_thread_id=f"calendar-write-graph-{uuid4()}",
                    ),
                )
                await AgentRunService(session).transition_status(
                    user_id=owner.id,
                    run_id=run.id,
                    target_status=AgentRunStatus.RUNNING,
                    current_node="prepare_write_action",
                )
                approval = await ApprovalService(session).create_request(
                    user_id=owner.id,
                    data=ApprovalRequestCreate(
                        agent_run_id=run.id,
                        action=action,
                        proposed_arguments=arguments,
                        idempotency_key=f"approval-{uuid4()}",
                    ),
                )
                await ApprovalService(session).decide(
                    user_id=owner.id,
                    approval_id=approval.id,
                    actor_user_id=owner.id,
                    decision=ApprovalDecision(status=ApprovalStatus.APPROVED),
                )
                return approval.id, build_write_idempotency_key(
                    approval_id=approval.id,
                    action=action,
                    version=approval.version,
                )

            create_arguments: dict[str, object] = {
                "title": "新建项目会议",
                "start_at": "2026-08-01T08:00:00Z",
                "end_at": "2026-08-01T09:00:00Z",
                "attendees": ["team@example.com"],
                "timezone": "Asia/Shanghai",
            }
            reschedule_arguments: dict[str, object] = {
                "event_id": str(reschedule_seed.id),
                "new_start_at": "2026-08-01T12:00:00Z",
                "new_end_at": "2026-08-01T13:00:00Z",
            }
            cancel_arguments: dict[str, object] = {
                "event_id": str(cancel_seed.id),
            }
            conflicting_arguments: dict[str, object] = {
                "title": "冲突会议",
                "start_at": "2026-08-01T10:30:00Z",
                "end_at": "2026-08-01T10:45:00Z",
                "attendees": [],
                "timezone": "Asia/Shanghai",
            }
            create_approval_id, create_key = await create_approved_action(
                ApprovalAction.CREATE_EVENT,
                create_arguments,
            )
            reschedule_approval_id, reschedule_key = await create_approved_action(
                ApprovalAction.RESCHEDULE_EVENT,
                reschedule_arguments,
            )
            cancel_approval_id, cancel_key = await create_approved_action(
                ApprovalAction.CANCEL_EVENT,
                cancel_arguments,
            )
            conflict_approval_id, conflict_key = await create_approved_action(
                ApprovalAction.CREATE_EVENT,
                conflicting_arguments,
            )
            owner_id = owner.id
            reschedule_event_id = reschedule_seed.id
            cancel_event_id = cancel_seed.id

        async with write_calendar_server.app.router.lifespan_context(write_calendar_server.app):
            calendar_connection = {"calendar": _connections(owner_id)["calendar"]}
            calendar_connection["calendar"]["httpx_client_factory"] = _http_factory(
                write_calendar_server.app
            )
            client = MultiServerMCPClient(calendar_connection)
            tools = {tool.name: tool for tool in await client.get_tools()}

            create_payload = {
                **create_arguments,
                "approval_id": str(create_approval_id),
                "idempotency_key": create_key,
            }
            created = _structured_result(await tools["create_event"].ainvoke(create_payload))
            created_again = _structured_result(await tools["create_event"].ainvoke(create_payload))
            conflict_result = await tools["create_event"].ainvoke(
                {
                    **conflicting_arguments,
                    "approval_id": str(conflict_approval_id),
                    "idempotency_key": conflict_key,
                }
            )
            rescheduled = _structured_result(
                await tools["reschedule_event"].ainvoke(
                    {
                        **reschedule_arguments,
                        "approval_id": str(reschedule_approval_id),
                        "idempotency_key": reschedule_key,
                    }
                )
            )
            cancelled = _structured_result(
                await tools["cancel_event"].ainvoke(
                    {
                        **cancel_arguments,
                        "approval_id": str(cancel_approval_id),
                        "idempotency_key": cancel_key,
                    }
                )
            )
            cancelled_again = _structured_result(
                await tools["cancel_event"].ainvoke(
                    {
                        **cancel_arguments,
                        "approval_id": str(cancel_approval_id),
                        "idempotency_key": cancel_key,
                    }
                )
            )
            assert created["reused"] is False
            assert created_again["reused"] is True
            assert created["event"]["id"] == created_again["event"]["id"]
            assert rescheduled["event"]["start_at"] == "2026-08-01T12:00:00Z"
            assert cancelled["event"]["status"] == "cancelled"
            assert cancelled_again["reused"] is True
            assert "CALENDAR_SCHEDULE_CONFLICT" in _result_text(conflict_result)

        async with session_factory() as session:
            rescheduled_event = await session.get(CalendarEvent, reschedule_event_id)
            cancelled_event = await session.get(CalendarEvent, cancel_event_id)
            failed_approval = await session.get(ApprovalRequest, conflict_approval_id)
            logs = list(
                (
                    await session.scalars(
                        select(ToolCallLog).where(ToolCallLog.user_id == owner_id)
                    )
                ).all()
            )
            assert rescheduled_event is not None
            assert rescheduled_event.start_at == datetime(
                2026,
                8,
                1,
                12,
                0,
                tzinfo=UTC,
            )
            assert cancelled_event is not None
            assert cancelled_event.status is CalendarEventStatus.CANCELLED
            assert failed_approval is not None
            assert failed_approval.status is ApprovalStatus.EXECUTION_FAILED
            assert {log.tool_name for log in logs} == {
                "create_event",
                "reschedule_event",
                "cancel_event",
            }
            assert len(logs) == 4
            assert sum(log.status is ToolCallStatus.SUCCEEDED for log in logs) == 3
            assert sum(log.status is ToolCallStatus.FAILED for log in logs) == 1
    finally:
        if owner_id is not None:
            async with session_factory() as session:
                await session.execute(delete(User).where(User.id == owner_id))
                await session.commit()
        await engine.dispose()


async def test_approved_send_email_executes_once_and_reuses_result(monkeypatch) -> None:
    """真实 MCP Server 必须重新核验审批，并让重复发送只产生一封出站邮件。"""

    database_url = os.getenv("TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("未配置 TEST_DATABASE_URL，跳过 MCP 写操作集成测试")

    engine = create_async_engine(database_url, poolclass=NullPool)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    write_mail_server = reload(mail_server)
    monkeypatch.setattr(write_mail_server, "AsyncSessionFactory", session_factory)
    owner_id: UUID | None = None

    try:
        async with session_factory() as session:
            email = f"mcp-write-{uuid4().hex}@example.com"
            owner = await UserService(session).create_user(
                UserCreate(
                    email=email,
                    password="mcp-write-password",
                    full_name="MCP 写操作用户",
                    timezone="Asia/Shanghai",
                )
            )
            imported = await EmailService(session).import_inbound_email(
                user_id=owner.id,
                data=EmailMessageImport(
                    provider="local",
                    thread_external_id=f"mcp-write-thread-{uuid4()}",
                    message_external_id=f"mcp-write-message-{uuid4()}",
                    subject="确认参加会议",
                    sender="manager@example.com",
                    recipients=[email],
                    body_text="请确认参加。",
                    sent_at=datetime(2026, 7, 24, 8, 0, tzinfo=UTC),
                ),
            )
            draft = await EmailService(session).create_draft(
                user_id=owner.id,
                data=EmailDraftCreate(
                    thread_id=imported.thread.id,
                    recipients=["manager@example.com"],
                    body_text="我会准时参加。",
                    idempotency_key=f"draft-{uuid4()}",
                ),
            )
            run = await AgentRunService(session).create_run(
                user_id=owner.id,
                data=AgentRunCreate(
                    email_thread_id=imported.thread.id,
                    graph_thread_id=f"mcp-write-graph-{uuid4()}",
                ),
            )
            await AgentRunService(session).transition_status(
                user_id=owner.id,
                run_id=run.id,
                target_status=AgentRunStatus.RUNNING,
                current_node="prepare_write_action",
            )
            approval = await ApprovalService(session).create_request(
                user_id=owner.id,
                data=ApprovalRequestCreate(
                    agent_run_id=run.id,
                    action=ApprovalAction.SEND_EMAIL,
                    proposed_arguments={"draft_message_id": str(draft.message.id)},
                    idempotency_key=f"approval-{uuid4()}",
                ),
            )
            await ApprovalService(session).decide(
                user_id=owner.id,
                approval_id=approval.id,
                actor_user_id=owner.id,
                decision=ApprovalDecision(status=ApprovalStatus.APPROVED),
            )
            owner_id = owner.id
            approval_id = approval.id
            draft_id = draft.message.id
            write_key = build_write_idempotency_key(
                approval_id=approval.id,
                action=approval.action,
                version=approval.version,
            )

        async with write_mail_server.app.router.lifespan_context(write_mail_server.app):
            mail_connection = {"mail": _connections(owner_id)["mail"]}
            mail_connection["mail"]["httpx_client_factory"] = _http_factory(write_mail_server.app)
            client = MultiServerMCPClient(mail_connection)
            tools = {tool.name: tool for tool in await client.get_tools()}
            arguments = {
                "draft_message_id": str(draft_id),
                "approval_id": str(approval_id),
                "idempotency_key": write_key,
            }
            first = _structured_result(await tools["send_email"].ainvoke(arguments))
            repeated = _structured_result(await tools["send_email"].ainvoke(arguments))
            mismatched = await tools["send_email"].ainvoke(
                {
                    **arguments,
                    "draft_message_id": str(uuid4()),
                }
            )

            assert first["reused"] is False
            assert repeated["reused"] is True
            assert first["message"]["id"] == repeated["message"]["id"]
            assert "APPROVAL_EXECUTION_ARGUMENTS_MISMATCH" in _result_text(mismatched)

        async with session_factory() as session:
            persisted_approval = await session.get(ApprovalRequest, approval_id)
            logs = list(
                (
                    await session.scalars(
                        select(ToolCallLog).where(
                            ToolCallLog.user_id == owner_id,
                            ToolCallLog.idempotency_key == write_key,
                        )
                    )
                ).all()
            )
            outbound_count = await session.scalar(
                select(func.count())
                .select_from(EmailMessage)
                .where(
                    EmailMessage.user_id == owner_id,
                    EmailMessage.direction == EmailDirection.OUTBOUND,
                    EmailMessage.idempotency_key == write_key,
                )
            )
            audit_actions = list(
                (
                    await session.scalars(
                        select(AuditLog.action).where(
                            AuditLog.user_id == owner_id,
                            AuditLog.approval_request_id == approval_id,
                        )
                    )
                ).all()
            )

            assert persisted_approval is not None
            assert persisted_approval.status is ApprovalStatus.EXECUTED
            assert persisted_approval.execution_result is not None
            assert len(logs) == 1
            assert logs[0].status is ToolCallStatus.SUCCEEDED
            assert outbound_count == 1
            assert audit_actions.count("tool.execution_started") == 1
            assert audit_actions.count("tool.execution_succeeded") == 1
    finally:
        if owner_id is not None:
            async with session_factory() as session:
                await session.execute(delete(User).where(User.id == owner_id))
                await session.commit()
        await engine.dispose()
