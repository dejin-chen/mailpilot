"""通过真实 HTTP、LangGraph、MCP 和 PostgreSQL 运行 36 条端到端评测。"""

from __future__ import annotations

import argparse
import asyncio
import json
import selectors
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter
from typing import Any
from uuid import uuid4

import httpx
from app.agent.schemas import EmailAction, EmailCategory, EmailPriority
from app.schemas.user import UserCreate
from app.services.user import UserService
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from evals.dataset import DATASET_VERSION, DEFAULT_DATASET_PATH, load_cases
from evals.evaluators.metrics import build_summary, evaluate_case
from evals.reporting import write_report
from evals.schemas import (
    EvalActual,
    EvalToolCall,
    EvaluationReport,
    MailEvalCase,
)

TERMINAL_STATUSES = {"completed", "ignored", "waiting_approval", "cancelled", "failed"}


class E2eHttpRunner:
    """把版本化数据集作为真实 API 请求送入隔离的 MailPilot 环境。"""

    def __init__(
        self,
        *,
        api_url: str,
        database_url: str,
        compose_project: str,
        request_timeout_seconds: float,
    ) -> None:
        self._api_url = api_url.rstrip("/")
        self._database_url = database_url
        self._compose_project = compose_project
        self._request_timeout_seconds = request_timeout_seconds

    async def run(self, cases: list[MailEvalCase]) -> list[EvalActual]:
        email, password = await self._create_user()
        async with httpx.AsyncClient(
            base_url=self._api_url,
            timeout=httpx.Timeout(self._request_timeout_seconds),
        ) as client:
            login = await self._json(
                client,
                "POST",
                "/auth/login",
                json={"email": email, "password": password},
            )
            headers = {"Authorization": f"Bearer {login['data']['access_token']}"}
            await self._seed_calendar_conflict(client, headers=headers)
            results: list[EvalActual] = []
            for index, case in enumerate(cases, start=1):
                actual = await self._run_case(client, headers=headers, case=case)
                results.append(actual)
                print(
                    f"[{index:02d}/{len(cases)}] {case.case_id} "
                    f"status={actual.terminal_status} tokens={actual.total_tokens} "
                    f"latency={actual.latency_ms:.0f}ms"
                )
            return results

    async def _create_user(self) -> tuple[str, str]:
        suffix = uuid4().hex
        email = f"e2e-{suffix}@example.com"
        password = "e2e-benchmark-password"
        engine = create_async_engine(self._database_url, pool_pre_ping=True)
        session_factory = async_sessionmaker(engine, expire_on_commit=False)
        try:
            async with session_factory() as session:
                await UserService(session).create_user(
                    UserCreate(
                        email=email,
                        password=password,
                        full_name="真实模型端到端评测用户",
                        timezone="Asia/Shanghai",
                    )
                )
        finally:
            await engine.dispose()
        return email, password

    async def _seed_calendar_conflict(
        self,
        client: httpx.AsyncClient,
        *,
        headers: dict[str, str],
    ) -> None:
        await self._json(
            client,
            "POST",
            "/calendar/events/import",
            headers=headers,
            json={
                "provider": "local",
                "external_id": f"e2e-conflict-{uuid4()}",
                "title": "MP-011 预置冲突会议",
                "start_at": "2026-07-29T07:00:00Z",
                "end_at": "2026-07-29T08:00:00Z",
                "timezone": "Asia/Shanghai",
                "attendees": [],
                "idempotency_key": f"e2e-conflict-{uuid4()}",
            },
        )

    async def _run_case(
        self,
        client: httpx.AsyncClient,
        *,
        headers: dict[str, str],
        case: MailEvalCase,
    ) -> EvalActual:
        started = perf_counter()
        stopped_service = self._failure_service(case)
        try:
            imported = await self._json(
                client,
                "POST",
                "/emails/import",
                headers=headers,
                json={
                    "provider": "local",
                    "thread_external_id": f"e2e-{case.case_id}-{uuid4()}",
                    "message_external_id": f"e2e-message-{case.case_id}-{uuid4()}",
                    "subject": case.input.subject,
                    "sender": str(case.input.sender),
                    "recipients": [str(item) for item in case.input.recipients],
                    "cc": [],
                    "body_text": case.input.body,
                    "sent_at": case.input.sent_at.isoformat(),
                },
            )
            thread_id = imported["data"]["thread"]["id"]
            if stopped_service and case.case_id != "MP-035":
                await self._set_service(stopped_service, running=False)
            started_run = await self._json(
                client,
                "POST",
                f"/emails/{thread_id}/process",
                headers=headers,
            )
            run_id = started_run["data"]["run"]["id"]
            run = await self._wait_for_terminal(client, headers=headers, run_id=run_id)
            approval_seen = run["status"] == "waiting_approval"
            approval_id: str | None = None
            if approval_seen:
                approval = await self._pending_approval(
                    client, headers=headers, run_id=run_id
                )
                approval_id = approval["id"]
                run = await self._apply_scenario_decision(
                    client,
                    headers=headers,
                    case=case,
                    run_id=run_id,
                    approval=approval,
                    stopped_service=stopped_service,
                )
            return self._to_actual(
                case,
                run,
                approval_seen=approval_seen,
                approval_id=approval_id,
                latency_ms=(perf_counter() - started) * 1000,
            )
        except Exception as exc:
            return EvalActual(
                case_id=case.case_id,
                terminal_status="failed",
                task_completed=False,
                latency_ms=(perf_counter() - started) * 1000,
                error_code=type(exc).__name__,
            )
        finally:
            if stopped_service:
                await self._set_service(stopped_service, running=True)

    async def _apply_scenario_decision(
        self,
        client: httpx.AsyncClient,
        *,
        headers: dict[str, str],
        case: MailEvalCase,
        run_id: str,
        approval: dict[str, Any],
        stopped_service: str | None,
    ) -> dict[str, Any]:
        decision = case.scenario.approval_decision
        approval_id = approval["id"]
        if decision == "pending":
            return await self._get_run(client, headers=headers, run_id=run_id)
        if decision == "rejected":
            await self._json(
                client,
                "POST",
                f"/approvals/{approval_id}/reject",
                headers=headers,
                json={"feedback": "端到端评测：用户拒绝执行"},
            )
        elif decision == "feedback":
            await self._json(
                client,
                "POST",
                f"/approvals/{approval_id}/request-regeneration",
                headers=headers,
                json={"feedback": "请使用更简洁、专业的语气重新生成"},
            )
        elif decision == "modified":
            arguments = dict(approval["proposed_arguments"])
            if case.case_id == "MP-027":
                arguments["title"] = f"修改后的产品沟通会-{uuid4().hex[:6]}"
            await self._json(
                client,
                "POST",
                f"/approvals/{approval_id}/approve-with-modifications",
                headers=headers,
                json={"modified_arguments": arguments},
            )
        elif decision == "approved":
            if case.case_id == "MP-035" and stopped_service:
                await self._set_service(stopped_service, running=False)
            await self._json(
                client,
                "POST",
                f"/approvals/{approval_id}/approve",
                headers=headers,
            )
            if case.scenario.duplicate_resume:
                await self._json(
                    client,
                    "POST",
                    f"/approvals/{approval_id}/approve",
                    headers=headers,
                )
        return await self._wait_for_terminal(client, headers=headers, run_id=run_id)

    async def _pending_approval(
        self,
        client: httpx.AsyncClient,
        *,
        headers: dict[str, str],
        run_id: str,
    ) -> dict[str, Any]:
        response = await self._json(
            client,
            "GET",
            "/approvals",
            headers=headers,
            params={"status": "pending", "limit": 100},
        )
        return next(
            item for item in response["data"]["items"] if item["agent_run_id"] == run_id
        )

    async def _wait_for_terminal(
        self,
        client: httpx.AsyncClient,
        *,
        headers: dict[str, str],
        run_id: str,
    ) -> dict[str, Any]:
        deadline = asyncio.get_running_loop().time() + self._request_timeout_seconds
        while True:
            run = await self._get_run(client, headers=headers, run_id=run_id)
            if run["status"] in TERMINAL_STATUSES and self._snapshot_is_materialized(run):
                return run
            if asyncio.get_running_loop().time() >= deadline:
                raise TimeoutError(f"AgentRun {run_id} 在限定时间内没有结束")
            await asyncio.sleep(0.25)

    @staticmethod
    def _snapshot_is_materialized(run: dict[str, Any]) -> bool:
        """避免把审批已落库、AgentRun 分析快照尚未写回的瞬间当作终态。"""

        if run.get("status") != "waiting_approval":
            return True
        result = run.get("result")
        return bool(
            isinstance(result, dict)
            and isinstance(result.get("classification"), dict)
            and isinstance(result.get("intent"), dict)
            and isinstance(result.get("plan"), dict)
            and int(run.get("total_tokens") or 0) > 0
        )

    async def _get_run(
        self,
        client: httpx.AsyncClient,
        *,
        headers: dict[str, str],
        run_id: str,
    ) -> dict[str, Any]:
        response = await self._json(
            client, "GET", f"/agent-runs/{run_id}", headers=headers
        )
        return response["data"]

    async def _set_service(self, service: str, *, running: bool) -> None:
        container = f"{self._compose_project}-{service}-1"
        command = "start" if running else "stop"
        result = await asyncio.to_thread(
            subprocess.run,
            ["docker", command, container],
            check=False,
            capture_output=True,
            text=True,
            timeout=60,
        )
        if result.returncode != 0:
            raise RuntimeError(f"无法{command}测试容器 {container}")
        if running:
            health_port = 58001 if service == "mail-mcp" else 58002
            async with httpx.AsyncClient(timeout=5) as health_client:
                for _ in range(40):
                    try:
                        response = await health_client.get(
                            f"http://127.0.0.1:{health_port}/health"
                        )
                        if response.status_code == 200:
                            return
                    except httpx.HTTPError:
                        pass
                    await asyncio.sleep(0.25)
            raise TimeoutError(f"测试容器 {container} 启动后没有恢复健康")

    @staticmethod
    def _failure_service(case: MailEvalCase) -> str | None:
        if case.case_id == "MP-019":
            return "calendar-mcp"
        if case.case_id in {"MP-020", "MP-035"}:
            return "mail-mcp"
        return None

    @staticmethod
    async def _json(
        client: httpx.AsyncClient,
        method: str,
        url: str,
        **kwargs: Any,
    ) -> dict[str, Any]:
        response = await client.request(method, url, **kwargs)
        response.raise_for_status()
        payload = response.json()
        if not payload.get("success"):
            raise RuntimeError(str(payload.get("error")))
        return payload

    @staticmethod
    def _to_actual(
        case: MailEvalCase,
        run: dict[str, Any],
        *,
        approval_seen: bool,
        approval_id: str | None,
        latency_ms: float,
    ) -> EvalActual:
        result = run.get("result") or {}
        classification = result.get("classification") or {}
        intent = result.get("intent") or {}
        meeting = intent.get("meeting") or {}
        tool_results = result.get("tool_results") or []
        execution = result.get("execution") or {}
        draft = result.get("draft") or {}
        tool_calls = [
            EvalToolCall(
                name=str(item.get("tool_name")),
                success=bool(item.get("success")),
                latency_ms=float(item.get("latency_ms") or 0),
            )
            for item in tool_results
            if item.get("tool_name")
        ]
        write_tool_call = None
        if execution.get("tool_name"):
            write_tool_call = EvalToolCall(
                name=str(execution["tool_name"]),
                success=run["status"] == "completed",
                latency_ms=0,
            )
        graph_status = result.get("run_status")
        terminal_status = {
            "success": "completed",
            "ignored": "ignored",
            "failed": "failed",
            "cancelled": "cancelled",
        }.get(str(graph_status), str(run["status"]))
        return EvalActual(
            case_id=case.case_id,
            action=(
                EmailAction(classification["action"])
                if classification.get("action")
                else None
            ),
            priority=(
                EmailPriority(classification["priority"])
                if classification.get("priority")
                else None
            ),
            category=(
                EmailCategory(classification["category"])
                if classification.get("category")
                else None
            ),
            meeting_detected=meeting.get("detected"),
            time_information_complete=meeting.get("time_information_complete"),
            needs_clarification=intent.get("needs_clarification"),
            tool_calls=tool_calls,
            write_tool_call=write_tool_call,
            approval_required=approval_seen,
            approval_intercepted=approval_seen,
            terminal_status=terminal_status,
            task_completed=terminal_status not in {"failed", "cancelled"},
            latency_ms=latency_ms,
            input_tokens=int(run.get("input_tokens") or 0),
            output_tokens=int(run.get("output_tokens") or 0),
            total_tokens=int(run.get("total_tokens") or 0),
            draft_text=draft.get("body_text"),
            error_code=run.get("error_code"),
        )


async def async_main(args: argparse.Namespace) -> tuple[Path, Path]:
    cases = load_cases(args.dataset)
    if args.limit is not None:
        cases = cases[: args.limit]
    if args.recalculate_from is not None:
        saved = json.loads(args.recalculate_from.read_text(encoding="utf-8"))
        actuals = [EvalActual.model_validate(item["actual"]) for item in saved["cases"]]
    else:
        actuals = await E2eHttpRunner(
            api_url=args.api_url,
            database_url=args.database_url,
            compose_project=args.compose_project,
            request_timeout_seconds=args.timeout,
        ).run(cases)
    actual_by_id = {item.case_id: item for item in actuals}
    results = [evaluate_case(case, actual_by_id[case.case_id]) for case in cases]
    report = EvaluationReport(
        report_name="MailPilot 真实模型 HTTP 端到端评测",
        mode="model",
        dataset_version=f"{DATASET_VERSION}-e2e",
        generated_at=datetime.now(UTC),
        summary=build_summary(results),
        cases=results,
        notes=[
            "通过真实 FastAPI、LangGraph、MCP、PostgreSQL 和 Redis 执行。",
            "邮箱与日历 Provider 为本地 PostgreSQL，不访问外部真实平台。",
            "人工审批由数据集预设决策自动提交，延迟不包含人工思考时间。",
            "MP-019、MP-020 和 MP-035 通过停止对应 MCP 容器注入故障。",
        ],
    )
    return write_report(report, args.output_dir)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="运行 MailPilot 真实 HTTP 端到端评测")
    parser.add_argument("--api-url", default="http://127.0.0.1:58000/api/v1")
    parser.add_argument("--database-url")
    parser.add_argument("--compose-project", default="mailpilot-benchmark")
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET_PATH)
    parser.add_argument("--output-dir", type=Path, default=Path("evals/reports/e2e-http"))
    parser.add_argument("--timeout", type=float, default=180)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--recalculate-from", type=Path)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.recalculate_from is None and not args.database_url:
        raise ValueError("实际执行端到端评测时必须提供 --database-url")
    loop_factory = lambda: asyncio.SelectorEventLoop(selectors.SelectSelector())  # noqa: E731
    json_path, markdown_path = asyncio.run(
        async_main(args), loop_factory=loop_factory
    )
    print(f"JSON 报告：{json_path}")
    print(f"Markdown 报告：{markdown_path}")


if __name__ == "__main__":
    main()
