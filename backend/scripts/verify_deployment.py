"""通过 Nginx 公共入口验证 MailPilot 部署，不执行任何业务写操作。"""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin
from urllib.request import Request, urlopen


@dataclass(frozen=True)
class EndpointCheck:
    """一项只读 HTTP 验收。"""

    path: str
    expected_status: int
    description: str
    expected_text: str | None = None


CHECKS = (
    EndpointCheck("/healthz", 200, "Nginx 网关", "mailpilot-gateway-ok"),
    EndpointCheck("/api/v1/health/live", 200, "FastAPI 存活检查", '"status":"ok"'),
    EndpointCheck(
        "/api/v1/health/ready",
        200,
        "PostgreSQL/Redis 就绪检查",
        '"postgresql":{"status":"up"',
    ),
    EndpointCheck("/_stcore/health", 200, "Streamlit 健康检查", "ok"),
    EndpointCheck("/openapi.json", 200, "OpenAPI 文档", '"title":"MailPilot"'),
    EndpointCheck("/api/v1/users/me", 401, "JWT 保护接口"),
    EndpointCheck("/mcp", 404, "MCP 公网隔离"),
)


def check_endpoint(base_url: str, check: EndpointCheck, timeout: float) -> str | None:
    """返回失败原因；HTTPError 的状态码也属于可验证响应。"""

    url = urljoin(base_url.rstrip("/") + "/", check.path.lstrip("/"))
    request = Request(url, headers={"User-Agent": "MailPilot-Deployment-Check/1.0"})
    try:
        with urlopen(request, timeout=timeout) as response:
            status = response.status
            body = response.read().decode("utf-8", errors="replace")
    except HTTPError as exc:
        status = exc.code
        body = exc.read().decode("utf-8", errors="replace")
    except URLError as exc:
        return f"连接失败：{type(exc.reason).__name__}"

    if status != check.expected_status:
        return f"状态码应为 {check.expected_status}，实际为 {status}"
    if body.lstrip().startswith(("{", "[")):
        try:
            compact_body = json.dumps(
                json.loads(body),
                ensure_ascii=False,
                separators=(",", ":"),
            )
        except json.JSONDecodeError:
            return "响应声称是 JSON，但格式无效"
    else:
        compact_body = body
    if check.expected_text and check.expected_text not in compact_body:
        return "响应内容不符合预期"
    return None


def main() -> None:
    parser = argparse.ArgumentParser(description="验证 MailPilot Nginx 公共入口")
    parser.add_argument("--base-url", default="http://localhost:8080")
    parser.add_argument("--timeout", type=float, default=5.0)
    args = parser.parse_args()

    failures: list[str] = []
    for check in CHECKS:
        failure = check_endpoint(args.base_url, check, args.timeout)
        if failure:
            failures.append(f"{check.description}：{failure}")
            print(f"[失败] {check.description}：{failure}")
        else:
            print(f"[通过] {check.description}")

    if failures:
        raise SystemExit(1)
    print("部署只读验收全部通过。")


if __name__ == "__main__":
    main()
