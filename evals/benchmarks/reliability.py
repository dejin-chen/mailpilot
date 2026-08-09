"""运行审批、写操作幂等和进程崩溃故障注入实验。

该脚本只允许连接专用测试数据库。它不会调用真实邮箱或日历平台，
所有副作用都写入 MailPilot 的 PostgreSQL 本地 Provider。
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import json
import os
import selectors
import subprocess
import sys
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from time import perf_counter
from typing import Any
from uuid import UUID, uuid4

from app.mcp.execution import execute_approved_write
from app.mcp.security import McpIdentity
from app.models.agent_run import AgentRunStatus
from app.models.approval import ApprovalAction, ApprovalRequest, ApprovalStatus
from app.models.audit import AuditLog
from app.models.calendar import CalendarEvent
from app.models.email import EmailDirection, EmailMessage
from app.models.tool_call import ToolCallLog
from app.schemas.agent_run import AgentRunCreate
from app.schemas.approval import ApprovalDecision, ApprovalRequestCreate
from app.schemas.email import EmailDraftCreate, EmailMessageImport
from app.schemas.user import UserCreate
from app.services.agent_run import AgentRunService
from app.services.approval import ApprovalService
from app.services.approved_tool_execution import (
    ApprovedToolExecutionService,
    build_write_idempotency_key,
)
from app.services.calendar import CalendarService
from app.services.email import EmailService
from app.services.exceptions import ToolExecutionInProgressError
from app.services.user import UserService
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

REPORT_DIR = Path(__file__).resolve().parents[1] / "reports"
REPORT_JSON = REPORT_DIR / "reliability-100-concurrency.json"
REPORT_MARKDOWN = REPORT_DIR / "reliability-100-concurrency.md"


@dataclass(slots=True)
class Scenario:
    """一次经过审批的本地写操作实验输入。"""

    action: str
    user_id: str
    approval_id: str
    agent_run_id: str
    idempotency_key: str
    arguments: dict[str, Any]


@dataclass(slots=True)
class RoundResult:
    """一轮 100 路并发实验的可审计结果。"""

    action: str
    round_number: int
    concurrency: int
    approval_successes: int
    write_successes: int
    write_in_progress: int
    unexpected_errors: list[str]
    business_side_effect_count: int
    tool_log_count: int
    approval_audit_count: int
    execution_started_audit_count: int
    execution_succeeded_audit_count: int
    final_reuse_confirmed: bool
    elapsed_ms: float

    @property
    def passed(self) -> bool:
        return (
            self.approval_successes == self.concurrency
            and self.write_successes == 1
            and self.write_in_progress == self.concurrency - 1
            and not self.unexpected_errors
            and self.business_side_effect_count == 1
            and self.tool_log_count == 1
            and self.approval_audit_count == 1
            and self.execution_started_audit_count == 1
            and self.execution_succeeded_audit_count == 1
            and self.final_reuse_confirmed
        )


@dataclass(slots=True)
class FaultResult:
    """业务已提交但进程尚未登记成功时的崩溃实验结果。"""

    action: str
    worker_exit_code: int
    business_side_effect_count_after_crash: int
    tool_log_status_after_crash: str | None
    approval_status_after_crash: str | None
    retry_blocked: bool
    business_side_effect_count_after_retry: int
    workflow_auto_recovered: bool

    @property
    def duplicate_prevented(self) -> bool:
        return (
            self.worker_exit_code == 86
            and self.business_side_effect_count_after_crash == 1
            and self.retry_blocked
            and self.business_side_effect_count_after_retry == 1
        )


def _database_url() -> str:
    value = os.getenv("TEST_DATABASE_URL") or os.getenv("DATABASE_URL")
    if not value:
        raise RuntimeError("必须配置 TEST_DATABASE_URL")
    if "benchmark" not in value and "test" not in value:
        raise RuntimeError("可靠性实验只允许连接名称包含 benchmark 或 test 的数据库")
    return value


def _session_factory():
    engine = create_async_engine(
        _database_url(),
        pool_size=20,
        max_overflow=20,
        pool_pre_ping=True,
    )
    return engine, async_sessionmaker(engine, expire_on_commit=False)


async def _seed_scenario(session_factory, action: ApprovalAction) -> Scenario:
    suffix = uuid4().hex
    async with session_factory() as session:
        user = await UserService(session).create_user(
            UserCreate(
                email=f"benchmark-{suffix}@example.com",
                password="benchmark-only-password",
                full_name="可靠性测试用户",
                timezone="Asia/Shanghai",
            )
        )
        imported = await EmailService(session).import_inbound_email(
            user_id=user.id,
            data=EmailMessageImport(
                provider="local",
                thread_external_id=f"benchmark-thread-{suffix}",
                message_external_id=f"benchmark-message-{suffix}",
                subject="可靠性测试邮件",
                sender="manager@example.com",
                recipients=[user.email],
                body_text="这是一条专用测试数据，不会发送到真实平台。",
                sent_at=datetime.now(UTC),
            ),
        )
        run = await AgentRunService(session).create_run(
            user_id=user.id,
            data=AgentRunCreate(
                email_thread_id=imported.thread.id,
                graph_thread_id=f"benchmark-graph-{suffix}",
            ),
        )
        await AgentRunService(session).transition_status(
            user_id=user.id,
            run_id=run.id,
            target_status=AgentRunStatus.RUNNING,
            current_node="prepare_write_action",
        )

        if action is ApprovalAction.SEND_EMAIL:
            draft = await EmailService(session).create_draft(
                user_id=user.id,
                data=EmailDraftCreate(
                    thread_id=imported.thread.id,
                    recipients=["manager@example.com"],
                    subject="Re: 可靠性测试邮件",
                    body_text="已确认，这是可靠性测试草稿。",
                    idempotency_key=f"benchmark-draft-{suffix}",
                ),
            )
            arguments: dict[str, Any] = {"draft_message_id": str(draft.message.id)}
        elif action is ApprovalAction.CREATE_EVENT:
            slot = datetime(2031, 1, 1, 1, 0, tzinfo=UTC) + timedelta(minutes=int(suffix[:4], 16))
            arguments = {
                "title": f"可靠性测试会议-{suffix[:8]}",
                "start_at": slot.isoformat(),
                "end_at": (slot + timedelta(minutes=30)).isoformat(),
                "attendees": ["manager@example.com"],
                "timezone": "Asia/Shanghai",
            }
        else:
            raise ValueError(f"暂不支持的实验动作：{action.value}")

        approval = await ApprovalService(session).create_request(
            user_id=user.id,
            data=ApprovalRequestCreate(
                agent_run_id=run.id,
                action=action,
                proposed_arguments=arguments,
                idempotency_key=f"benchmark-approval-{suffix}",
            ),
            request_id=f"seed-{suffix}",
        )
        key = build_write_idempotency_key(
            approval_id=approval.id,
            action=action,
            version=approval.version,
        )
        return Scenario(
            action=action.value,
            user_id=str(user.id),
            approval_id=str(approval.id),
            agent_run_id=str(run.id),
            idempotency_key=key,
            arguments=arguments,
        )


async def _concurrent_approve(session_factory, scenario: Scenario, concurrency: int) -> int:
    ready = asyncio.Event()

    async def decide(index: int) -> bool:
        await ready.wait()
        async with session_factory() as session:
            result = await ApprovalService(session).decide(
                user_id=UUID(scenario.user_id),
                approval_id=UUID(scenario.approval_id),
                actor_user_id=UUID(scenario.user_id),
                decision=ApprovalDecision(status=ApprovalStatus.APPROVED),
                request_id=f"concurrent-approval-{index}",
            )
            return result.status in {
                ApprovalStatus.APPROVED,
                ApprovalStatus.EXECUTING,
                ApprovalStatus.EXECUTED,
            }

    tasks = [asyncio.create_task(decide(index)) for index in range(concurrency)]
    ready.set()
    results = await asyncio.gather(*tasks, return_exceptions=True)
    return sum(result is True for result in results)


async def _business_operation(session, scenario: Scenario) -> dict[str, object]:
    action = ApprovalAction(scenario.action)
    user_id = UUID(scenario.user_id)
    if action is ApprovalAction.SEND_EMAIL:
        result = await EmailService(session).send_draft(
            user_id=user_id,
            draft_message_id=UUID(str(scenario.arguments["draft_message_id"])),
            idempotency_key=scenario.idempotency_key,
        )
        return {
            "success": True,
            "message_id": str(result.message.id),
            "reused": result.reused,
        }
    if action is ApprovalAction.CREATE_EVENT:
        result = await CalendarService(session).create_event(
            user_id=user_id,
            title=str(scenario.arguments["title"]),
            start_at=datetime.fromisoformat(str(scenario.arguments["start_at"])),
            end_at=datetime.fromisoformat(str(scenario.arguments["end_at"])),
            attendees=[str(item) for item in scenario.arguments["attendees"]],
            timezone=str(scenario.arguments["timezone"]),
            idempotency_key=scenario.idempotency_key,
        )
        return {
            "success": True,
            "event_id": str(result.event.id),
            "reused": result.reused,
        }
    raise ValueError(f"暂不支持的实验动作：{scenario.action}")


async def _execute_once(session_factory, scenario: Scenario, request_id: str) -> dict[str, object]:
    return await execute_approved_write(
        session_factory=session_factory,
        identity=McpIdentity(user_id=UUID(scenario.user_id), request_id=request_id),
        approval_id=UUID(scenario.approval_id),
        action=ApprovalAction(scenario.action),
        arguments=scenario.arguments,
        idempotency_key=scenario.idempotency_key,
        operation=lambda session: _business_operation(session, scenario),
    )


async def _side_effect_count(session_factory, scenario: Scenario) -> int:
    async with session_factory() as session:
        model = (
            EmailMessage if scenario.action == ApprovalAction.SEND_EMAIL.value else CalendarEvent
        )
        statement = (
            select(func.count())
            .select_from(model)
            .where(
                model.user_id == UUID(scenario.user_id),
                model.idempotency_key == scenario.idempotency_key,
            )
        )
        if model is EmailMessage:
            statement = statement.where(EmailMessage.direction == EmailDirection.OUTBOUND)
        return int(await session.scalar(statement) or 0)


async def _audit_count(session_factory, scenario: Scenario, action: str) -> int:
    async with session_factory() as session:
        statement = (
            select(func.count())
            .select_from(AuditLog)
            .where(
                AuditLog.user_id == UUID(scenario.user_id),
                AuditLog.approval_request_id == UUID(scenario.approval_id),
                AuditLog.action == action,
            )
        )
        return int(await session.scalar(statement) or 0)


async def _tool_log_snapshot(session_factory, scenario: Scenario) -> tuple[int, str | None]:
    async with session_factory() as session:
        logs = list(
            await session.scalars(
                select(ToolCallLog).where(
                    ToolCallLog.user_id == UUID(scenario.user_id),
                    ToolCallLog.idempotency_key == scenario.idempotency_key,
                )
            )
        )
        return len(logs), logs[0].status.value if logs else None


async def _approval_status(session_factory, scenario: Scenario) -> str | None:
    async with session_factory() as session:
        approval = await session.get(ApprovalRequest, UUID(scenario.approval_id))
        return approval.status.value if approval is not None else None


async def _run_round(
    session_factory,
    *,
    action: ApprovalAction,
    round_number: int,
    concurrency: int,
) -> RoundResult:
    started = perf_counter()
    scenario = await _seed_scenario(session_factory, action)
    approval_successes = await _concurrent_approve(session_factory, scenario, concurrency)
    ready = asyncio.Event()

    async def invoke(index: int):
        await ready.wait()
        return await _execute_once(session_factory, scenario, f"concurrent-write-{index}")

    tasks = [asyncio.create_task(invoke(index)) for index in range(concurrency)]
    ready.set()
    results = await asyncio.gather(*tasks, return_exceptions=True)
    successes = sum(isinstance(result, dict) for result in results)
    in_progress = sum(isinstance(result, ToolExecutionInProgressError) for result in results)
    unexpected = sorted(
        {type(result).__name__ for result in results if isinstance(result, Exception)}
        - {"ToolExecutionInProgressError"}
    )
    reused = await _execute_once(session_factory, scenario, "post-concurrency-reuse")
    tool_log_count, _ = await _tool_log_snapshot(session_factory, scenario)
    return RoundResult(
        action=action.value,
        round_number=round_number,
        concurrency=concurrency,
        approval_successes=approval_successes,
        write_successes=successes,
        write_in_progress=in_progress,
        unexpected_errors=unexpected,
        business_side_effect_count=await _side_effect_count(session_factory, scenario),
        tool_log_count=tool_log_count,
        approval_audit_count=await _audit_count(session_factory, scenario, "approval.approved"),
        execution_started_audit_count=await _audit_count(
            session_factory, scenario, "tool.execution_started"
        ),
        execution_succeeded_audit_count=await _audit_count(
            session_factory, scenario, "tool.execution_succeeded"
        ),
        final_reuse_confirmed=bool(reused.get("reused")),
        elapsed_ms=(perf_counter() - started) * 1000,
    )


def _encode_scenario(scenario: Scenario) -> str:
    raw = json.dumps(asdict(scenario), ensure_ascii=False).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii")


def _decode_scenario(value: str) -> Scenario:
    raw = base64.urlsafe_b64decode(value.encode("ascii"))
    return Scenario(**json.loads(raw.decode("utf-8")))


async def _fault_worker(encoded_scenario: str) -> None:
    """模拟 MCP 在业务提交后、登记成功前被强制终止。"""

    scenario = _decode_scenario(encoded_scenario)
    engine, session_factory = _session_factory()
    try:
        async with session_factory() as session:
            await ApprovedToolExecutionService(session).begin(
                user_id=UUID(scenario.user_id),
                approval_id=UUID(scenario.approval_id),
                action=ApprovalAction(scenario.action),
                arguments=scenario.arguments,
                idempotency_key=scenario.idempotency_key,
                request_id="fault-worker",
            )
        async with session_factory() as session:
            await _business_operation(session, scenario)
        # 故意不调用 succeed，也不进行优雅清理，等价于进程突然崩溃。
        os._exit(86)
    finally:
        await engine.dispose()


async def _run_fault_case(session_factory, action: ApprovalAction) -> FaultResult:
    scenario = await _seed_scenario(session_factory, action)
    await _concurrent_approve(session_factory, scenario, 1)
    process = subprocess.run(
        [
            sys.executable,
            "-m",
            "evals.benchmarks.reliability",
            "--fault-worker",
            _encode_scenario(scenario),
        ],
        cwd=Path(__file__).resolve().parents[2],
        check=False,
        capture_output=True,
        text=True,
        timeout=60,
    )
    side_effect_after_crash = await _side_effect_count(session_factory, scenario)
    _, log_status = await _tool_log_snapshot(session_factory, scenario)
    approval_status = await _approval_status(session_factory, scenario)
    retry_blocked = False
    try:
        await _execute_once(session_factory, scenario, "after-process-restart")
    except ToolExecutionInProgressError:
        retry_blocked = True
    side_effect_after_retry = await _side_effect_count(session_factory, scenario)
    return FaultResult(
        action=action.value,
        worker_exit_code=process.returncode,
        business_side_effect_count_after_crash=side_effect_after_crash,
        tool_log_status_after_crash=log_status,
        approval_status_after_crash=approval_status,
        retry_blocked=retry_blocked,
        business_side_effect_count_after_retry=side_effect_after_retry,
        workflow_auto_recovered=False,
    )


def _write_report(
    rounds: list[RoundResult],
    faults: list[FaultResult],
    *,
    started_at: datetime,
) -> None:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    logical_operations = len(rounds) + len(faults)
    duplicate_side_effects = sum(
        max(item.business_side_effect_count - 1, 0) for item in rounds
    ) + sum(max(item.business_side_effect_count_after_retry - 1, 0) for item in faults)
    report = {
        "report_name": "MailPilot 100 路并发与进程崩溃可靠性实验",
        "started_at": started_at.isoformat(),
        "generated_at": datetime.now(UTC).isoformat(),
        "logical_operations": logical_operations,
        "duplicate_side_effects": duplicate_side_effects,
        "duplicate_side_effect_rate": (
            duplicate_side_effects / logical_operations if logical_operations else 0
        ),
        "concurrency_rounds_passed": sum(item.passed for item in rounds),
        "concurrency_rounds_total": len(rounds),
        "fault_duplicate_prevention_passed": sum(item.duplicate_prevented for item in faults),
        "fault_cases_total": len(faults),
        "workflow_auto_recovery_passed": sum(item.workflow_auto_recovered for item in faults),
        "rounds": [{**asdict(item), "passed": item.passed} for item in rounds],
        "faults": [
            {**asdict(item), "duplicate_prevented": item.duplicate_prevented} for item in faults
        ],
        "notes": [
            "业务副作用只写入专用 PostgreSQL 本地 Provider，不访问真实邮箱或日历平台。",
            "并发请求未获得 Redis 锁时返回执行中；并发结束后再次请求必须复用原结果。",
            "故障注入点位于业务事务已提交、ToolCallLog 尚未登记成功之间。",
            (
                "当前实现会阻止崩溃后的盲目重试，因此保证不重复，"
                "但不能自动把 running 状态修复为 succeeded。"
            ),
        ],
    }
    REPORT_JSON.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    lines = [
        "# MailPilot 100 路并发与进程崩溃可靠性实验",
        "",
        (
            f"- 并发轮次：{report['concurrency_rounds_passed']}/"
            f"{report['concurrency_rounds_total']} 通过"
        ),
        (
            f"- 崩溃后防重复：{report['fault_duplicate_prevention_passed']}/"
            f"{report['fault_cases_total']} 通过"
        ),
        (
            f"- 崩溃后自动恢复：{report['workflow_auto_recovery_passed']}/"
            f"{report['fault_cases_total']} 通过"
        ),
        f"- 重复副作用：{duplicate_side_effects}",
        f"- 重复副作用率：{report['duplicate_side_effect_rate'] * 100:.2f}%",
        "",
        "## 并发明细",
        "",
        "| 操作 | 轮次 | 审批成功 | 写成功 | 执行中 | 副作用数 | 结果 |",
        "|---|---:|---:|---:|---:|---:|---|",
    ]
    lines.extend(
        f"| {item.action} | {item.round_number} | {item.approval_successes}/{item.concurrency} "
        f"| {item.write_successes} | {item.write_in_progress} | "
        f"{item.business_side_effect_count} | {'通过' if item.passed else '失败'} |"
        for item in rounds
    )
    lines.extend(
        [
            "",
            "## 进程崩溃明细",
            "",
            "| 操作 | 子进程退出码 | 崩溃后副作用 | 重试后副作用 | 阻止盲目重试 | 自动恢复 |",
            "|---|---:|---:|---:|---|---|",
        ]
    )
    lines.extend(
        f"| {item.action} | {item.worker_exit_code} | "
        f"{item.business_side_effect_count_after_crash} | "
        f"{item.business_side_effect_count_after_retry} | "
        f"{'是' if item.retry_blocked else '否'} | "
        f"{'是' if item.workflow_auto_recovered else '否'} |"
        for item in faults
    )
    lines.extend(["", "## 说明", ""])
    lines.extend(f"- {note}" for note in report["notes"])
    REPORT_MARKDOWN.write_text("\n".join(lines) + "\n", encoding="utf-8")


async def _run(concurrency: int, rounds_per_action: int) -> None:
    started_at = datetime.now(UTC)
    engine, session_factory = _session_factory()
    try:
        rounds: list[RoundResult] = []
        for action in (ApprovalAction.SEND_EMAIL, ApprovalAction.CREATE_EVENT):
            for round_number in range(1, rounds_per_action + 1):
                result = await _run_round(
                    session_factory,
                    action=action,
                    round_number=round_number,
                    concurrency=concurrency,
                )
                rounds.append(result)
                print(
                    f"{action.value} 第 {round_number} 轮："
                    f"{'通过' if result.passed else '失败'}，"
                    f"副作用={result.business_side_effect_count}"
                )
        faults = [
            await _run_fault_case(session_factory, action)
            for action in (ApprovalAction.SEND_EMAIL, ApprovalAction.CREATE_EVENT)
        ]
        _write_report(rounds, faults, started_at=started_at)
        print(f"JSON 报告：{REPORT_JSON}")
        print(f"Markdown 报告：{REPORT_MARKDOWN}")
    finally:
        await engine.dispose()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="运行 MailPilot 可靠性 Benchmark")
    parser.add_argument("--concurrency", type=int, default=100)
    parser.add_argument("--rounds", type=int, default=10)
    parser.add_argument("--fault-worker")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    loop_factory = lambda: asyncio.SelectorEventLoop(selectors.SelectSelector())  # noqa: E731
    if args.fault_worker:
        asyncio.run(_fault_worker(args.fault_worker), loop_factory=loop_factory)
        return
    if args.concurrency < 2 or args.rounds < 1:
        raise ValueError("concurrency 至少为 2，rounds 至少为 1")
    asyncio.run(_run(args.concurrency, args.rounds), loop_factory=loop_factory)


if __name__ == "__main__":
    main()
