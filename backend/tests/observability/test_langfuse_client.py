"""Langfuse v4 适配器的无网络测试。"""

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any
from uuid import uuid4

from app.observability.langfuse_client import LangfuseObservability


class FakeRawObservation:
    def __init__(self) -> None:
        self.updates: list[dict[str, object]] = []

    def update(self, **kwargs: object) -> None:
        self.updates.append(kwargs)


class FakeLangfuseClient:
    def __init__(self, **kwargs: object) -> None:
        self.init_kwargs = kwargs
        self.starts: list[dict[str, object]] = []
        self.raw = FakeRawObservation()
        self.flushed = False
        self.closed = False

    def create_trace_id(self, *, seed: str) -> str:
        return ("0" * 31 + str(len(seed) % 10))[-32:]

    @contextmanager
    def start_as_current_observation(
        self,
        **kwargs: object,
    ) -> Iterator[FakeRawObservation]:
        self.starts.append(kwargs)
        yield self.raw

    def flush(self) -> None:
        self.flushed = True

    def shutdown(self) -> None:
        self.closed = True


def test_langfuse_adapter_uses_stable_trace_and_hides_content() -> None:
    captured: dict[str, Any] = {}

    def factory(**kwargs: object) -> FakeLangfuseClient:
        client = FakeLangfuseClient(**kwargs)
        captured["client"] = client
        return client

    observer = LangfuseObservability(
        public_key="pk-test",
        secret_key="sk-test",
        base_url="https://langfuse.example.com",
        environment="test",
        release="0.1.0",
        capture_content=False,
        client_factory=factory,
    )
    run_id = uuid4()
    with observer.trace(
        name="mail_processing.execute",
        user_id=uuid4(),
        thread_id="thread-001",
        agent_run_id=run_id,
        request_id="request-001",
        input={"body": "客户机密正文"},
    ) as trace:
        trace.update(output={"draft": "机密草稿"})

    client = captured["client"]
    start = client.starts[0]
    assert start["trace_context"]["trace_id"]  # type: ignore[index]
    assert start["input"] == {"type": "mapping", "keys": ["body"]}
    assert "客户机密正文" not in str(start)
    assert "机密草稿" not in str(client.raw.updates)
    observer.flush()
    observer.shutdown()
    assert client.flushed is True
    assert client.closed is True


def test_langfuse_adapter_masks_content_when_capture_is_enabled() -> None:
    client = FakeLangfuseClient()
    observer = LangfuseObservability(
        public_key="pk-test",
        secret_key="sk-test",
        base_url=None,
        environment="test",
        release="0.1.0",
        capture_content=True,
        client_factory=lambda **_: client,
    )

    with observer.span(
        name="mcp.search_emails",
        input={"sender": "alice@example.com", "token": "secret"},
    ):
        pass

    start = client.starts[0]
    assert start["input"]["sender"] == "[邮箱已脱敏]"  # type: ignore[index]
    assert start["input"]["token"] == "[已脱敏]"  # type: ignore[index]
