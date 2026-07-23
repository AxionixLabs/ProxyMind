# -*- coding: utf-8 -*-

from types import SimpleNamespace
from unittest.mock import AsyncMock

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


def test_mcp_config_accepts_independent_startup_timeout(tmp_path) -> None:
    target = tmp_path / "mcp_servers.json"
    target.write_text(
        '{"mcpServers":{"remote":{"url":"https://example.test/mcp",'
        '"startup_timeout_sec":24}}}',
        encoding="utf-8",
    )

    servers = load_mcp_servers_file(tmp_path)

    assert servers[0]["startup_timeout_sec"] == 24


@pytest.mark.parametrize(
    ("command", "expected"),
    [
        ("/mcp", (True, None)),
        ("/MCP START", (True, "start")),
        ("/mcp force", (True, "force")),
        ("/mcp restart", (True, "restart")),
        ("/mcp unknown", (False, None)),
        ("hello", (False, None)),
    ],
)
def test_parse_mcp_command(command, expected) -> None:
    assert mcp.parse_mcp_command(command) == expected


@pytest.mark.anyio
async def test_background_mcp_start_never_restarts_runtime(monkeypatch) -> None:
    mind = SimpleNamespace(
        start_external_mcp_runtime=AsyncMock(),
        restart_external_mcp_runtime=AsyncMock(),
    )
    monkeypatch.setattr(
        mcp,
        "render_external_mcp_start_status",
        lambda _mind, **_kwargs: True,
    )

    await mcp.start_mcp_runtime(mind, include_disabled=True)

    mind.start_external_mcp_runtime.assert_awaited_once_with(
        include_disabled=True,
    )
    mind.restart_external_mcp_runtime.assert_not_awaited()


@pytest.mark.parametrize(
    ("snapshot", "expected"),
    [
        (
            {
                "done": True,
                "items": [
                    {"name": "docs", "state": "ready", "tools": 4},
                ],
            },
            "■ External MCP ready · 1/1 servers · 4 tools",
        ),
        (
            {
                "done": True,
                "items": [
                    {
                        "name": "docs",
                        "state": "failed",
                        "tools": 0,
                        "detail": "timeout",
                    },
                ],
            },
            "■ External MCP failed · 0/1 servers\n└ docs: timeout",
        ),
    ],
)
def test_external_mcp_start_result_is_committed_to_tui(
    snapshot,
    expected,
) -> None:
    views = []
    mind = SimpleNamespace(
        external_mcp=SimpleNamespace(last_start_snapshot=snapshot),
        frontend=SimpleNamespace(
            application=SimpleNamespace(emit=views.append),
        ),
    )

    assert mcp.render_external_mcp_start_status(mind)

    status = next(
        view for view in views
        if view.type == "tui.external_mcp.status"
    )
    assert status.renderable.plain_text == expected
