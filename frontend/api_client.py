"""Streamlit 调用 FastAPI 和订阅 SSE 的统一客户端。"""

import json
import os
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

import httpx


@dataclass(slots=True)
class ApiClientError(Exception):
    """可以直接展示给中文页面的 API 错误。"""

    message: str
    code: str = "API_ERROR"
    status_code: int | None = None

    def __str__(self) -> str:
        return self.message


class MailPilotApiClient:
    """只通过公开 HTTP API 访问 MailPilot，不直接读取数据库。"""

    def __init__(
        self,
        *,
        base_url: str | None = None,
        access_token: str | None = None,
    ) -> None:
        self.base_url = (
            base_url
            or os.getenv("MAILPILOT_API_URL", "http://localhost:8000/api/v1")
        ).rstrip("/")
        self.access_token = access_token

    def request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json_body: dict[str, Any] | None = None,
        timeout: float = 20.0,
    ) -> Any:
        """调用统一响应格式接口，并把网络和业务错误转成同一种异常。"""

        try:
            response = httpx.request(
                method,
                f"{self.base_url}{path}",
                params=params,
                json=json_body,
                headers=self._headers(),
                timeout=timeout,
            )
        except httpx.RequestError as exc:
            raise ApiClientError(
                message="无法连接 MailPilot 后端，请检查服务是否启动",
                code="BACKEND_UNAVAILABLE",
            ) from exc

        payload = self._read_json(response)
        if response.is_error or not payload.get("success", False):
            error = payload.get("error") or {}
            raise ApiClientError(
                message=str(error.get("message") or f"请求失败（HTTP {response.status_code}）"),
                code=str(error.get("code") or "API_ERROR"),
                status_code=response.status_code,
            )
        return payload.get("data")

    def login(self, *, email: str, password: str) -> dict[str, Any]:
        """使用邮箱密码获取 JWT。"""

        data = self.request(
            "POST",
            "/auth/login",
            json_body={"email": email, "password": password},
        )
        return dict(data)

    def stream_events(
        self,
        *,
        run_id: str,
        after: int = 0,
    ) -> Iterator[dict[str, Any]]:
        """服务端消费 SSE；断开后调用方可以把最后 sequence 再传回来。"""

        try:
            with httpx.stream(
                "GET",
                f"{self.base_url}/agent-runs/{run_id}/events",
                params={"after": after},
                headers={**self._headers(), "Accept": "text/event-stream"},
                timeout=httpx.Timeout(connect=10.0, read=60.0, write=10.0, pool=10.0),
            ) as response:
                if response.is_error:
                    response.read()
                    payload = self._read_json(response)
                    error = payload.get("error") or {}
                    raise ApiClientError(
                        message=str(error.get("message") or "订阅执行轨迹失败"),
                        code=str(error.get("code") or "SSE_ERROR"),
                        status_code=response.status_code,
                    )
                yield from self._parse_sse_lines(response.iter_lines())
        except httpx.RequestError as exc:
            raise ApiClientError(
                message="实时轨迹连接已中断，可使用最后事件序号重新连接",
                code="SSE_DISCONNECTED",
            ) from exc

    def _headers(self) -> dict[str, str]:
        headers = {"Accept": "application/json"}
        if self.access_token:
            headers["Authorization"] = f"Bearer {self.access_token}"
        return headers

    @staticmethod
    def _read_json(response: httpx.Response) -> dict[str, Any]:
        try:
            payload = response.json()
        except ValueError as exc:
            raise ApiClientError(
                message=f"后端返回了无法识别的响应（HTTP {response.status_code}）",
                code="INVALID_API_RESPONSE",
                status_code=response.status_code,
            ) from exc
        return dict(payload) if isinstance(payload, dict) else {}

    @staticmethod
    def _parse_sse_lines(lines: Iterator[str]) -> Iterator[dict[str, Any]]:
        event_type = "message"
        event_id: str | None = None
        data_lines: list[str] = []
        for line in lines:
            if line == "":
                if data_lines:
                    raw_data = "\n".join(data_lines)
                    try:
                        data: Any = json.loads(raw_data)
                    except json.JSONDecodeError:
                        data = {"raw": raw_data}
                    yield {"event": event_type, "id": event_id, "data": data}
                event_type = "message"
                event_id = None
                data_lines = []
                continue
            if line.startswith(":"):
                yield {"event": "heartbeat", "id": None, "data": {}}
            elif line.startswith("event:"):
                event_type = line.removeprefix("event:").strip()
            elif line.startswith("id:"):
                event_id = line.removeprefix("id:").strip()
            elif line.startswith("data:"):
                data_lines.append(line.removeprefix("data:").lstrip())
