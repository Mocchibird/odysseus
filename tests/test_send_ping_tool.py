"""Regression coverage for Iris's immediate ntfy ping tool."""

import asyncio
import json
from pathlib import Path

from src import tool_implementations
from src.agent_tools import TOOL_TAGS, ToolBlock
import src.tool_execution as tool_execution
from src.tool_execution import execute_tool_block
from src.tool_index import ALWAYS_AVAILABLE, ASSISTANT_ALWAYS_AVAILABLE, BUILTIN_TOOL_DESCRIPTIONS
from src.tool_parsing import _TOOL_NAME_MAP
from src.tool_security import is_public_blocked_tool, plan_mode_disabled_tools

ROOT = Path(__file__).resolve().parents[1]


def test_send_ping_registered_everywhere():
    assert "send_ping" in TOOL_TAGS
    assert "send_ping" in ALWAYS_AVAILABLE
    assert "send_ping" in ASSISTANT_ALWAYS_AVAILABLE
    assert "send_ping" in BUILTIN_TOOL_DESCRIPTIONS
    assert _TOOL_NAME_MAP["ping"] == "send_ping"
    assert _TOOL_NAME_MAP["notify"] == "send_ping"
    assert is_public_blocked_tool("send_ping") is True
    assert "send_ping" in plan_mode_disabled_tools()

    from src.tool_schemas import FUNCTION_TOOL_SCHEMAS

    names = {s["function"]["name"] for s in FUNCTION_TOOL_SCHEMAS}
    assert "send_ping" in names


def test_send_ping_uses_ntfy_integration_and_settings_topic():
    source = (ROOT / "src" / "tool_implementations.py").read_text()
    assert "async def do_send_ping" in source
    assert "resolve_ntfy_integration(" in source
    assert "load_integrations()" in source
    assert "get_user_setting(\"reminder_ntfy_topic\"" in source
    assert "settings.get(\"reminder_ntfy_topic\")" in source
    assert "send_ntfy_notification(" in source

    ntfy_source = (ROOT / "src" / "ntfy_client.py").read_text()
    assert "quote(clean_topic, safe='')" in ntfy_source
    assert "httpx.BasicAuth" in ntfy_source
    assert "\"Authorization\"" in ntfy_source
    assert "\"Bearer" in ntfy_source

    notes_source = (ROOT / "routes" / "note_routes.py").read_text()
    assert "send_ntfy_notification(" in notes_source
    assert "resolve_ntfy_integration(" in notes_source
    assert "get_user_setting" in notes_source
    assert "_setting(\"reminder_ntfy_topic\"" in notes_source


def test_send_ping_dispatches_through_tool_execution(monkeypatch):
    async def fake_do_send_ping(content, owner=None):
        return {"output": f"owner={owner} content={content}", "exit_code": 0}

    monkeypatch.setattr(tool_implementations, "do_send_ping", fake_do_send_ping)
    monkeypatch.setattr(tool_execution, "_owner_is_admin", lambda owner: True)

    desc, result = asyncio.run(execute_tool_block(
        ToolBlock("send_ping", json.dumps({"message": "test"})),
        owner="admin",
    ))

    assert desc == "send_ping"
    assert result["exit_code"] == 0
    assert "owner=admin" in result["output"]
