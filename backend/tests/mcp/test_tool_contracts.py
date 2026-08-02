"""FastMCP 工具发现合同测试。"""

import pytest

from mcp_servers.calendar.server import mcp as calendar_mcp
from mcp_servers.mail.server import mcp as mail_mcp


@pytest.mark.asyncio
async def test_mail_server_exposes_expected_tools_without_model_user_id() -> None:
    tools = {tool.name: tool for tool in await mail_mcp.list_tools()}

    assert set(tools) == {
        "get_email_thread",
        "search_emails",
        "create_email_draft",
        "send_email",
        "mark_email_processed",
    }
    assert all("user_id" not in tool.inputSchema.get("properties", {}) for tool in tools.values())
    assert tools["get_email_thread"].annotations.readOnlyHint is True
    assert tools["send_email"].annotations.destructiveHint is True
    assert {"approval_id", "idempotency_key"}.issubset(
        set(tools["send_email"].inputSchema["required"])
    )


@pytest.mark.asyncio
async def test_calendar_server_exposes_expected_tools_and_write_hints() -> None:
    tools = {tool.name: tool for tool in await calendar_mcp.list_tools()}

    assert set(tools) == {
        "check_availability",
        "find_available_slots",
        "create_event",
        "reschedule_event",
        "cancel_event",
    }
    assert all("user_id" not in tool.inputSchema.get("properties", {}) for tool in tools.values())
    assert tools["check_availability"].annotations.readOnlyHint is True
    assert tools["cancel_event"].annotations.destructiveHint is True
    for name in {"create_event", "reschedule_event", "cancel_event"}:
        assert {"approval_id", "idempotency_key"}.issubset(set(tools[name].inputSchema["required"]))
