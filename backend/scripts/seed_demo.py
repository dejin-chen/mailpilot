"""为本地开发创建可重复使用的中文演示数据。"""

import asyncio
import sys
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from app.core.config import get_settings
from app.db.session import AsyncSessionFactory, close_database
from app.repositories.user import UserRepository
from app.schemas.calendar import CalendarEventImport
from app.schemas.email import EmailMessageImport
from app.schemas.user import UserCreate
from app.services.calendar import CalendarService
from app.services.email import EmailService
from app.services.exceptions import (
    CalendarEventAlreadyImportedError,
    EmailAlreadyImportedError,
)
from app.services.user import UserService


async def seed_demo() -> None:
    """创建演示用户、两封邮件和两条日程；重复数据会被安全忽略。"""

    settings = get_settings()
    email = settings.demo_user_email.strip().lower()
    password = (
        settings.demo_user_password.get_secret_value()
        if settings.demo_user_password is not None
        else ""
    )
    if not password:
        msg = "请先在本地环境设置 DEMO_USER_PASSWORD，脚本不会使用硬编码密码"
        raise RuntimeError(msg)

    async with AsyncSessionFactory() as session:
        user_repository = UserRepository(session)
        user = await user_repository.get_by_email(email)
        if user is None:
            user = await UserService(session).create_user(
                UserCreate(
                    email=email,
                    password=password,
                    full_name="MailPilot 演示用户",
                    timezone="Asia/Shanghai",
                )
            )
        user_id = user.id

        timezone = ZoneInfo("Asia/Shanghai")
        base_time = datetime.now(timezone).replace(hour=10, minute=0, second=0, microsecond=0)
        if base_time <= datetime.now(timezone):
            base_time += timedelta(days=1)

        mail_service = EmailService(session)
        email_samples = [
            EmailMessageImport(
                provider="local",
                thread_external_id="demo-thread-project",
                message_external_id="demo-message-project-001",
                subject="请确认 MailPilot 项目演示时间",
                sender="manager@example.com",
                recipients=[email],
                body_text="请确认明天下午是否可以进行项目演示，并回复可用时间。",
                sent_at=base_time - timedelta(hours=2),
            ),
            EmailMessageImport(
                provider="local",
                thread_external_id="demo-thread-newsletter",
                message_external_id="demo-message-newsletter-001",
                subject="本周技术资讯",
                sender="newsletter@example.com",
                recipients=[email],
                body_text="这是一封无需回复的普通技术资讯邮件。",
                sent_at=base_time - timedelta(hours=1),
            ),
        ]
        imported_email_count = 0
        for sample in email_samples:
            try:
                await mail_service.import_inbound_email(user_id=user_id, data=sample)
                imported_email_count += 1
            except EmailAlreadyImportedError:
                pass

        calendar_service = CalendarService(session)
        event_samples = [
            CalendarEventImport(
                provider="local",
                external_id="demo-event-focus-001",
                title="专注开发时间",
                description="用于验证时间冲突检测。",
                start_at=base_time,
                end_at=base_time + timedelta(hours=1),
                timezone="Asia/Shanghai",
                attendees=[email],
                idempotency_key="demo-seed-event-focus-001",
            ),
            CalendarEventImport(
                provider="local",
                external_id="demo-event-team-001",
                title="团队同步会议",
                description="MailPilot 演示日历事件。",
                start_at=base_time + timedelta(hours=3),
                end_at=base_time + timedelta(hours=4),
                timezone="Asia/Shanghai",
                attendees=[email, "manager@example.com"],
                idempotency_key="demo-seed-event-team-001",
            ),
        ]
        imported_event_count = 0
        for sample in event_samples:
            try:
                await calendar_service.import_event(user_id=user_id, data=sample)
                imported_event_count += 1
            except CalendarEventAlreadyImportedError:
                pass

        print(
            f"演示数据准备完成：用户 {email}，"
            f"新增邮件 {imported_email_count} 封，新增日程 {imported_event_count} 条。"
        )


async def run() -> None:
    """在同一个事件循环中准备数据并释放数据库连接池。"""

    try:
        await seed_demo()
    finally:
        await close_database()


def main() -> None:
    """使用 Windows 兼容事件循环运行异步脚本。"""

    loop_factory = asyncio.SelectorEventLoop if sys.platform == "win32" else None
    asyncio.run(run(), loop_factory=loop_factory)


if __name__ == "__main__":
    main()
