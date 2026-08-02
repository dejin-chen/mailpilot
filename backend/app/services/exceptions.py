"""可被 API、MCP 等适配器统一映射的业务异常。"""

from app.core.exceptions import AppException


class UserAlreadyExistsError(AppException):
    """邮箱已经被其他用户使用。"""

    def __init__(self) -> None:
        super().__init__(
            code="USER_ALREADY_EXISTS",
            message="该邮箱已注册",
            status_code=409,
        )


class InvalidCredentialsError(AppException):
    """邮箱或密码不正确。"""

    def __init__(self) -> None:
        super().__init__(
            code="INVALID_CREDENTIALS",
            message="邮箱或密码不正确",
            status_code=401,
            headers={"WWW-Authenticate": "Bearer"},
        )


class InactiveUserError(AppException):
    """账号已被停用。"""

    def __init__(self) -> None:
        super().__init__(
            code="USER_INACTIVE",
            message="账号已被停用",
            status_code=403,
        )


class EmailAlreadyImportedError(AppException):
    """同一用户已经导入过相同 Provider 外部 ID 的邮件。"""

    def __init__(self) -> None:
        super().__init__(
            code="EMAIL_ALREADY_IMPORTED",
            message="该邮件已经导入，请勿重复提交",
            status_code=409,
        )


class EmailThreadNotFoundError(AppException):
    """当前用户无权访问或不存在指定邮件线程。"""

    def __init__(self) -> None:
        super().__init__(
            code="EMAIL_THREAD_NOT_FOUND",
            message="邮件线程不存在",
            status_code=404,
        )


class EmailImportConflictError(AppException):
    """并发导入重试后仍出现无法自动解决的数据冲突。"""

    def __init__(self) -> None:
        super().__init__(
            code="EMAIL_IMPORT_CONFLICT",
            message="邮件导入发生数据冲突，请稍后重试",
            status_code=409,
        )


class EmailDraftConflictError(AppException):
    """草稿幂等键已被其他消息占用或并发创建无法安全复用。"""

    def __init__(self) -> None:
        super().__init__(
            code="EMAIL_DRAFT_CONFLICT",
            message="邮件草稿发生幂等冲突，请检查幂等键",
            status_code=409,
        )


class EmailDraftNotFoundError(AppException):
    """当前用户无权访问、找不到或目标不是邮件草稿。"""

    def __init__(self) -> None:
        super().__init__(
            code="EMAIL_DRAFT_NOT_FOUND",
            message="邮件草稿不存在",
            status_code=404,
        )


class EmailSendConflictError(AppException):
    """发送幂等键被其他邮件占用，不能判断是否可安全复用。"""

    def __init__(self) -> None:
        super().__init__(
            code="EMAIL_SEND_CONFLICT",
            message="邮件发送发生幂等冲突",
            status_code=409,
        )


class ApprovalRequiredError(AppException):
    """有外部副作用的工具尚未获得人工审批。"""

    def __init__(self, operation: str) -> None:
        super().__init__(
            code="APPROVAL_REQUIRED",
            message=f"操作 {operation} 必须先完成人工审批",
            status_code=409,
        )


class CalendarEventAlreadyImportedError(AppException):
    """相同外部 ID 或幂等键的日历事件已经存在。"""

    def __init__(self) -> None:
        super().__init__(
            code="CALENDAR_EVENT_ALREADY_IMPORTED",
            message="该日历事件已经导入，请勿重复提交",
            status_code=409,
        )


class CalendarEventNotFoundError(AppException):
    """当前用户无权访问或不存在指定日历事件。"""

    def __init__(self) -> None:
        super().__init__(
            code="CALENDAR_EVENT_NOT_FOUND",
            message="日历事件不存在",
            status_code=404,
        )


class CalendarImportConflictError(AppException):
    """并发导入重试后仍有无法自动解决的数据冲突。"""

    def __init__(self) -> None:
        super().__init__(
            code="CALENDAR_IMPORT_CONFLICT",
            message="日历事件导入发生数据冲突，请稍后重试",
            status_code=409,
        )


class InvalidCalendarRangeError(AppException):
    """日历查询时间缺少时区或结束时间不晚于开始时间。"""

    def __init__(self) -> None:
        super().__init__(
            code="INVALID_CALENDAR_RANGE",
            message="时间范围无效，结束时间必须晚于开始时间且包含时区",
            status_code=422,
        )


class CalendarScheduleConflictError(AppException):
    """创建或修改会议时，目标时间与现有日程冲突。"""

    def __init__(self) -> None:
        super().__init__(
            code="CALENDAR_SCHEDULE_CONFLICT",
            message="目标时间与现有日程冲突",
            status_code=409,
        )


class CalendarWriteConflictError(AppException):
    """日历写操作幂等键冲突或事件状态不允许修改。"""

    def __init__(self) -> None:
        super().__init__(
            code="CALENDAR_WRITE_CONFLICT",
            message="日历写操作发生冲突",
            status_code=409,
        )


class AgentRunNotFoundError(AppException):
    """当前用户无权访问或不存在指定 AgentRun。"""

    def __init__(self) -> None:
        super().__init__(
            code="AGENT_RUN_NOT_FOUND",
            message="Agent 执行记录不存在",
            status_code=404,
        )


class AgentRunConflictError(AppException):
    """graph_thread_id 已被占用或并发创建冲突。"""

    def __init__(self) -> None:
        super().__init__(
            code="AGENT_RUN_CONFLICT",
            message="Agent 执行记录发生冲突",
            status_code=409,
        )


class AgentRunInvalidTransitionError(AppException):
    """AgentRun 状态不允许当前迁移。"""

    def __init__(self) -> None:
        super().__init__(
            code="AGENT_RUN_INVALID_TRANSITION",
            message="Agent 执行状态不允许当前操作",
            status_code=409,
        )


class ApprovalRequestNotFoundError(AppException):
    """当前用户无权访问或不存在指定审批单。"""

    def __init__(self) -> None:
        super().__init__(
            code="APPROVAL_REQUEST_NOT_FOUND",
            message="审批请求不存在",
            status_code=404,
        )


class ApprovalRequestConflictError(AppException):
    """审批幂等键或运行版本发生冲突。"""

    def __init__(self) -> None:
        super().__init__(
            code="APPROVAL_REQUEST_CONFLICT",
            message="审批请求发生幂等冲突",
            status_code=409,
        )


class ApprovalAlreadyDecidedError(AppException):
    """非 pending 审批不能再次作出用户决定。"""

    def __init__(self) -> None:
        super().__init__(
            code="APPROVAL_ALREADY_DECIDED",
            message="该审批已经处理，不能重复决定",
            status_code=409,
        )


class ApprovalArgumentsInvalidError(AppException):
    """修改后接受时的参数不符合对应写工具 Schema。"""

    def __init__(self, details: object | None = None) -> None:
        super().__init__(
            code="APPROVAL_ARGUMENTS_INVALID",
            message="修改后的审批参数无效",
            status_code=422,
            details=details,
        )


class AgentCheckpointNotFoundError(AppException):
    """审批记录存在，但找不到对应 LangGraph 暂停存档。"""

    def __init__(self) -> None:
        super().__init__(
            code="AGENT_CHECKPOINT_NOT_FOUND",
            message="找不到该审批对应的工作流存档",
            status_code=409,
        )


class AgentCheckpointConflictError(AppException):
    """thread_id 指向的存档和当前审批单不一致。"""

    def __init__(self) -> None:
        super().__init__(
            code="AGENT_CHECKPOINT_CONFLICT",
            message="工作流存档与当前审批请求不匹配",
            status_code=409,
        )


class AgentResumeError(AppException):
    """审批已保存，但本次工作流恢复失败，可安全重试相同决定。"""

    def __init__(self) -> None:
        super().__init__(
            code="AGENT_RESUME_ERROR",
            message="审批决定已保存，但工作流暂时无法恢复，请重试相同操作",
            status_code=503,
        )


class AgentWorkflowExecutionError(AppException):
    """完整邮件 Agent 工作流发生未预期异常。"""

    def __init__(self) -> None:
        super().__init__(
            code="AGENT_WORKFLOW_EXECUTION_ERROR",
            message="Agent 工作流执行失败",
            status_code=500,
        )


class AgentCheckpointerUnavailableError(AppException):
    """FastAPI 尚未获得可用的持久化 Checkpointer。"""

    def __init__(self) -> None:
        super().__init__(
            code="AGENT_CHECKPOINTER_UNAVAILABLE",
            message="工作流存档服务暂时不可用",
            status_code=503,
        )


class ApprovalExecutionNotAllowedError(AppException):
    """审批尚未接受、已拒绝或要求重新生成，不能执行写操作。"""

    def __init__(self) -> None:
        super().__init__(
            code="APPROVAL_EXECUTION_NOT_ALLOWED",
            message="当前审批状态不允许执行写操作",
            status_code=409,
        )


class ApprovalExecutionArgumentsMismatchError(AppException):
    """MCP 收到的写参数和用户最终审批参数不一致。"""

    def __init__(self) -> None:
        super().__init__(
            code="APPROVAL_EXECUTION_ARGUMENTS_MISMATCH",
            message="工具参数与用户审批内容不一致",
            status_code=409,
        )


class ToolExecutionInProgressError(AppException):
    """同一幂等写操作已有执行者，禁止并发重复调用。"""

    def __init__(self) -> None:
        super().__init__(
            code="TOOL_EXECUTION_IN_PROGRESS",
            message="该写操作正在执行，请勿重复调用",
            status_code=409,
        )


class WriteSafetyControlUnavailableError(AppException):
    """写操作依赖的分布式安全锁暂时不可用。"""

    def __init__(self) -> None:
        super().__init__(
            code="WRITE_SAFETY_CONTROL_UNAVAILABLE",
            message="写操作安全控制暂时不可用，已阻止本次执行",
            status_code=503,
        )


class ToolExecutionAlreadyFailedError(AppException):
    """相同写操作已经失败，不能自动盲目重试。"""

    def __init__(self) -> None:
        super().__init__(
            code="TOOL_EXECUTION_ALREADY_FAILED",
            message="该写操作已经失败，需要核对后再决定是否重试",
            status_code=409,
        )


class ToolExecutionUncertainError(AppException):
    """超时等情况导致外部副作用结果暂时无法确定。"""

    def __init__(self) -> None:
        super().__init__(
            code="TOOL_EXECUTION_UNCERTAIN",
            message="写操作结果暂时无法确定，禁止自动重试",
            status_code=409,
        )


class MemoryProfileNotFoundError(AppException):
    """当前用户无权访问或不存在指定长期记忆。"""

    def __init__(self) -> None:
        super().__init__(
            code="MEMORY_PROFILE_NOT_FOUND",
            message="长期记忆不存在",
            status_code=404,
        )


class MemoryProfileConflictError(AppException):
    """同一用户下相同类型和 key 的记忆已经存在。"""

    def __init__(self) -> None:
        super().__init__(
            code="MEMORY_PROFILE_CONFLICT",
            message="相同类型和 key 的长期记忆已经存在",
            status_code=409,
        )


class MemoryProfileVersionConflictError(AppException):
    """调用方依据的旧版本已不是数据库中的当前版本。"""

    def __init__(self, current_version: int | None = None) -> None:
        details = {"current_version": current_version} if current_version is not None else None
        super().__init__(
            code="MEMORY_PROFILE_VERSION_CONFLICT",
            message="长期记忆已被其他操作修改，请刷新后重试",
            status_code=409,
            details=details,
        )


class MemoryProfileValueInvalidError(AppException):
    """记忆内容或逻辑 key 不符合对应记忆类型的规则。"""

    def __init__(self, details: object | None = None) -> None:
        super().__init__(
            code="MEMORY_PROFILE_VALUE_INVALID",
            message="长期记忆内容无效",
            status_code=422,
            details=details,
        )


class AgentMemoryStoreUnavailableError(AppException):
    """长期记忆 Store 未初始化、连接失败或内容无法安全读取。"""

    def __init__(self) -> None:
        super().__init__(
            code="AGENT_MEMORY_STORE_UNAVAILABLE",
            message="长期记忆服务暂时不可用",
            status_code=503,
        )
