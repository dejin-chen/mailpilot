"""外部邮件与日历平台的可替换能力接口。"""

from app.providers.calendar import CalendarProvider, LocalCalendarProvider
from app.providers.mail import LocalMailProvider, MailProvider

__all__ = [
    "CalendarProvider",
    "LocalCalendarProvider",
    "LocalMailProvider",
    "MailProvider",
]
