"""从正式邮件 Service 读取当前用户最新收件消息的节点。"""

import logging
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol
from uuid import UUID

from langgraph.runtime import Runtime
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.context import AgentRuntimeContext
from app.agent.exceptions import LatestInboundEmailNotFoundError
from app.agent.nodes.common import NodeUpdate, failure_update
from app.agent.schemas import AgentRunStatus
from app.agent.state import MailAgentState
from app.core.exceptions import AppException
from app.db.session import AsyncSessionFactory
from app.models.email import EmailDirection
from app.services.email import EmailService

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class LoadedInboundEmail:
    """load_email 节点需要的最小、可序列化邮件快照。"""

    message_id: UUID
    subject: str
    body_text: str
    sender: str
    recipients: tuple[str, ...]
    cc: tuple[str, ...]
    sent_at: datetime


class EmailThreadReader(Protocol):
    """生产 Service Reader 和测试 Fake Reader 的共同接口。"""

    async def get_latest_inbound(
        self,
        *,
        user_id: UUID,
        thread_id: UUID,
    ) -> LoadedInboundEmail: ...


SessionFactory = Callable[[], AbstractAsyncContextManager[AsyncSession]]


class ServiceEmailThreadReader:
    """通过 EmailService 读取数据库邮件，不在节点中复制查询规则。"""

    def __init__(self, session_factory: SessionFactory = AsyncSessionFactory) -> None:
        self._session_factory = session_factory

    async def get_latest_inbound(
        self,
        *,
        user_id: UUID,
        thread_id: UUID,
    ) -> LoadedInboundEmail:
        async with self._session_factory() as session:
            thread = await EmailService(session).get_thread(
                user_id=user_id,
                thread_id=thread_id,
            )

        inbound_messages = [
            message for message in thread.messages if message.direction is EmailDirection.INBOUND
        ]
        if not inbound_messages:
            raise LatestInboundEmailNotFoundError
        message = max(inbound_messages, key=lambda item: (item.sent_at, str(item.id)))
        return LoadedInboundEmail(
            message_id=message.id,
            subject=message.subject,
            body_text=message.body_text,
            sender=message.sender,
            recipients=tuple(message.recipients),
            cc=tuple(message.cc),
            sent_at=message.sent_at,
        )


class LoadEmailNode:
    """按可信用户身份加载邮件，并只返回本节点负责的 State 字段。"""

    name = "load_email"

    def __init__(self, reader: EmailThreadReader) -> None:
        self._reader = reader

    async def __call__(
        self,
        state: MailAgentState,
        runtime: Runtime[AgentRuntimeContext],
    ) -> NodeUpdate:
        context = runtime.context
        if context is None:
            return failure_update(
                node=self.name,
                code="AGENT_CONTEXT_MISSING",
                message="Agent 运行时身份缺失",
            )
        try:
            email = await self._reader.get_latest_inbound(
                user_id=context.user_id,
                thread_id=state["email_thread_id"],
            )
        except AppException as exc:
            return failure_update(node=self.name, code=exc.code, message=exc.message)
        except LatestInboundEmailNotFoundError:
            return failure_update(
                node=self.name,
                code="INBOUND_EMAIL_NOT_FOUND",
                message="邮件线程中没有可分析的收件消息",
            )
        except Exception as exc:
            logger.exception(
                "加载 Agent 邮件失败",
                extra={"node": self.name, "error_type": type(exc).__name__},
            )
            return failure_update(
                node=self.name,
                code="EMAIL_LOAD_ERROR",
                message="读取邮件失败",
                retryable=True,
            )

        return {
            "email_message_id": email.message_id,
            "email_subject": email.subject,
            "email_body": email.body_text,
            "sender": email.sender,
            "recipients": list(email.recipients),
            "cc": list(email.cc),
            "email_sent_at": email.sent_at,
            "current_node": self.name,
            "run_status": AgentRunStatus.RUNNING,
        }
