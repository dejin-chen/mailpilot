"""结构化日志测试。"""

import json
import logging

from app.core.logging import JsonFormatter, request_id_context


def test_json_formatter_adds_context_and_redacts_secret() -> None:
    token = request_id_context.set("request-test-001")
    try:
        record = logging.LogRecord(
            name="mailpilot.test",
            level=logging.INFO,
            pathname=__file__,
            lineno=10,
            msg="测试日志",
            args=(),
            exc_info=None,
        )
        record.password = "should-not-appear"
        record.access_token = "signed-jwt-should-not-appear"
        record.database_url = "postgresql://user:secret@database/mailpilot"
        record.total_tokens = 42
        record.operation = "health_check"

        payload = json.loads(JsonFormatter().format(record))

        assert payload["message"] == "测试日志"
        assert payload["request_id"] == "request-test-001"
        assert payload["password"] == "***"
        assert payload["access_token"] == "***"
        assert payload["database_url"] == "***"
        assert payload["total_tokens"] == 42
        assert payload["operation"] == "health_check"
        assert "should-not-appear" not in json.dumps(payload)
        assert "signed-jwt-should-not-appear" not in json.dumps(payload)
    finally:
        request_id_context.reset(token)
