# -*- coding: utf-8 -*-

import pytest

from mind_app.mcp.config import McpConfigError, load_mcp_servers_file
from mind_app.tui.features import mcp


class _Runtime(object):
    def __init__(self) -> None:
        self.request = None

    async def select_menu(self, request):
        self.request = request
        return None


@pytest.mark.anyio
async def test_mcp_menu_keeps_complete_actions_without_configuration(monkeypatch) -> None:
    runtime = _Runtime()
    monkeypatch.setattr(
        mcp,
        "summarize_external_runtime",
        lambda _mind: {
            "started": False,
            "configured": [],
            "config_error": "",
            "tool_groups": [],
            "tool_count": 0,
        },
    )

    await mcp.choose_mcp_action(runtime, object())

    assert [option.value for option in runtime.request.options] == [
        "start",
        "force",
        "stop",
        "restart",
        "status",
    ]
    assert [option.label for option in runtime.request.options] == [
        "start",
        "force",
        "stop",
        "restart",
        "status",
    ]
    assert runtime.request.selected == 4
    assert "enabled=false" in runtime.request.options[1].detail
    assert "stdio" in runtime.request.options[2].detail


def test_invalid_mcp_config_reports_location_without_content(tmp_path) -> None:
    target = tmp_path / "mcp_servers.json"
    target.write_text(
        '{"mcpServers":{"remote":{"transport":"streamable_http" '
        '"url":"https://example.test/secret"}}}',
        encoding="utf-8",
    )

    with pytest.raises(McpConfigError) as captured:
        load_mcp_servers_file(tmp_path)

    message = str(captured.value)
    assert "mcp_servers.json" in message
    assert "line 1, column" in message
    assert "secret" not in message


def test_mcp_config_accepts_standard_type_alias(tmp_path) -> None:
    target = tmp_path / "mcp_servers.json"
    target.write_text(
        '{"mcpServers":{"remote":{"type":"streamable-http",'
        '"url":"https://example.test/mcp"}}}',
        encoding="utf-8",
    )

    servers = load_mcp_servers_file(tmp_path)

    assert servers[0]["transport"] == "streamable_http"
