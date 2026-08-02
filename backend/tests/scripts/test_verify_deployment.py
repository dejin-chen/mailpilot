"""部署只读验收脚本测试。"""

from io import BytesIO
from pathlib import Path
from urllib.error import HTTPError

import pytest

from backend.scripts import verify_deployment
from backend.scripts.verify_deployment import EndpointCheck, check_endpoint

PROJECT_ROOT = Path(__file__).resolve().parents[3]


class _FakeResponse:
    def __init__(self, status: int, body: bytes) -> None:
        self.status = status
        self._body = body

    def __enter__(self) -> "_FakeResponse":
        return self

    def __exit__(self, *_: object) -> None:
        return None

    def read(self) -> bytes:
        return self._body


def test_check_endpoint_accepts_expected_json(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        verify_deployment,
        "urlopen",
        lambda *_args, **_kwargs: _FakeResponse(200, b'{"status": "ready"}'),
    )

    failure = check_endpoint(
        "http://localhost:8080",
        EndpointCheck("/ready", 200, "ready", '"status":"ready"'),
        1,
    )

    assert failure is None


def test_check_endpoint_can_verify_expected_401(monkeypatch: pytest.MonkeyPatch) -> None:
    error = HTTPError(
        "http://localhost:8080/api/v1/users/me",
        401,
        "Unauthorized",
        hdrs=None,
        fp=BytesIO(b'{"error":{"code":"AUTHENTICATION_REQUIRED"}}'),
    )
    def raise_http_error(*_args: object, **_kwargs: object) -> None:
        raise error

    monkeypatch.setattr(verify_deployment, "urlopen", raise_http_error)

    failure = check_endpoint(
        "http://localhost:8080",
        EndpointCheck("/api/v1/users/me", 401, "JWT"),
        1,
    )

    assert failure is None


def test_nginx_upstreams_use_docker_dns_dynamic_resolution() -> None:
    """后端或前端容器重建后，Nginx 不应继续使用旧容器 IP。"""

    config = (PROJECT_ROOT / "nginx" / "nginx.conf").read_text(encoding="utf-8")

    assert "resolver 127.0.0.11 valid=5s ipv6=off;" in config
    assert "zone mailpilot_backend 64k;" in config
    assert "server backend:8000 resolve;" in config
    assert "zone mailpilot_frontend 64k;" in config
    assert "server frontend:8501 resolve;" in config
