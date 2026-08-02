"""邮件导入请求与查询响应 Schema。"""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

from app.models.email import EmailDirection, EmailThreadStatus
from app.schemas.common import ProviderName


class EmailMessageImport(BaseModel):
    """从邮件 Provider 导入一封收件邮件所需的数据。"""

    provider: ProviderName = "local"
    thread_external_id: str = Field(min_length=1, max_length=255)
    message_external_id: str = Field(min_length=1, max_length=255)
    subject: str = Field(default="", max_length=500)
    sender: EmailStr
    recipients: list[EmailStr] = Field(min_length=1)
    cc: list[EmailStr] = Field(default_factory=list)
    body_text: str
    headers: dict[str, str] = Field(default_factory=dict)
    sent_at: datetime

    @field_validator("provider", mode="before")
    @classmethod
    def normalize_provider(cls, value: object) -> object:
        """Provider 名称统一去空格并转为小写。"""

        return value.strip().lower() if isinstance(value, str) else value

    @field_validator("thread_external_id", "message_external_id")
    @classmethod
    def normalize_external_id(cls, value: str) -> str:
        """外部 ID 不允许只有空格。"""

        normalized = value.strip()
        if not normalized:
            msg = "外部 ID 不能为空"
            raise ValueError(msg)
        return normalized

    @field_validator("subject")
    @classmethod
    def normalize_subject(cls, value: str) -> str:
        """保留无主题邮件，并移除主题两端的无意义空格。"""

        return value.strip()

    @field_validator("sender", mode="before")
    @classmethod
    def normalize_sender(cls, value: object) -> object:
        """发件人邮箱统一为小写。"""

        return value.strip().lower() if isinstance(value, str) else value

    @field_validator("recipients", "cc", mode="before")
    @classmethod
    def normalize_address_list(cls, value: object) -> object:
        """邮箱列表去空格并转为小写，便于稳定去重和查询。"""

        if not isinstance(value, list):
            return value
        return [item.strip().lower() if isinstance(item, str) else item for item in value]

    @field_validator("sent_at")
    @classmethod
    def require_timezone(cls, value: datetime) -> datetime:
        """拒绝没有时区的时间，避免跨地区会议和邮件排序出错。"""

        if value.utcoffset() is None:
            msg = "邮件时间必须包含时区"
            raise ValueError(msg)
        return value


class EmailMessageResponse(BaseModel):
    """单封邮件的公开响应数据。"""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    thread_id: UUID
    provider: str
    external_id: str
    subject: str
    sender: EmailStr
    recipients: list[EmailStr]
    cc: list[EmailStr]
    body_text: str
    direction: EmailDirection
    headers: dict[str, str]
    sent_at: datetime
    idempotency_key: str | None
    created_at: datetime
    updated_at: datetime


class EmailThreadResponse(BaseModel):
    """邮件线程列表中的摘要数据。"""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    provider: str
    external_id: str
    subject: str
    participants: list[EmailStr]
    status: EmailThreadStatus
    last_message_at: datetime
    created_at: datetime
    updated_at: datetime


class EmailThreadDetailResponse(EmailThreadResponse):
    """邮件线程详情及按发送时间排列的消息。"""

    messages: list[EmailMessageResponse]


class EmailImportResponse(BaseModel):
    """邮件导入成功结果。"""

    thread: EmailThreadResponse
    message: EmailMessageResponse
    created_thread: bool


class EmailDraftCreate(BaseModel):
    """在现有邮件线程中创建本地草稿。"""

    thread_id: UUID
    recipients: list[EmailStr] = Field(min_length=1)
    cc: list[EmailStr] = Field(default_factory=list)
    subject: str | None = Field(default=None, max_length=500)
    body_text: str = Field(min_length=1)
    idempotency_key: str = Field(min_length=1, max_length=255)

    @field_validator("recipients", "cc", mode="before")
    @classmethod
    def normalize_draft_addresses(cls, value: object) -> object:
        """草稿收件人统一去空格并转小写。"""

        if not isinstance(value, list):
            return value
        return [item.strip().lower() if isinstance(item, str) else item for item in value]

    @field_validator("subject")
    @classmethod
    def normalize_draft_subject(cls, value: str | None) -> str | None:
        """未提供主题时由 Service 继承线程主题。"""

        return value.strip() if value is not None else None

    @field_validator("body_text", "idempotency_key")
    @classmethod
    def reject_blank_draft_fields(cls, value: str) -> str:
        """草稿正文和幂等键不能只有空格。"""

        normalized = value.strip()
        if not normalized:
            msg = "草稿正文和幂等键不能为空"
            raise ValueError(msg)
        return normalized
