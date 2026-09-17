# -*- coding: utf-8 -*-

from types import SimpleNamespace
from unittest.mock import (
    AsyncMock,
    Mock,
)

import pytest
from prompt_toolkit.utils import get_cwidth
from metadata import const
from agent.domain.mcp_authorization import McpAuthorizationStatus
from agent.ports.mcp_runtime import (
    McpRuntimeSnapshot,
    McpServiceSnapshot,
)

from frontends.tui.features.tools import (
    print_available_tools,
    render_tools_summary
)


@pytest.mark.parametrize("width", [32, 80, 120])
def test_zero_tool_and_failed_services_use_runtime_authentication_and_wrap_hints(width):
    application = SimpleNamespace(emit=Mock())
    services = (
        McpServiceSnapshot("empty", "mcp__empty__", True, "ready", "streamable_http",
            authorization=McpAuthorizationStatus("anonymous", "missing", "accepted", 0)),
        McpServiceSnapshot("raw failure", "mcp__failed__", True, "failed", "streamable_http",
            authorization=McpAuthorizationStatus("not_logged_in", "missing", "rejected", 0, error="login_required")),
    )
    render_tools_summary(application=application, terminal_width=width, services=services, tools=[])
    text = "".join(value for _, value in application.emit.call_args_list[0].args[0].renderable.fragments)
    compact = " ".join(text.split())
    assert "Auth: anonymous" in compact and "Auth: not_logged_in" in compact
    assert "exact server name" in compact and '"raw failure"' in compact
    assert text.count("Tools: (none)") == 2
    assert all(get_cwidth(line) <= width for line in text.splitlines())


def test_tool_metadata_cannot_claim_authentication():
    application = SimpleNamespace(emit=Mock())
    render_tools_summary(application=application, terminal_width=120, tools=[{
        "name": "remote", "meta": {"external": True, "server": "remote", "auth": "Authenticated"},
    }])
    text = "".join(value for _, value in application.emit.call_args_list[0].args[0].renderable.fragments)
    assert "Auth: unknown" in text and "Authenticated" not in text


def test_tools_summary_renders_as_one_compact_block() -> None:
    application = SimpleNamespace(
        emit=Mock(),
        viewport=SimpleNamespace(width=120),
    )
    tools = [
        {
            "name": "external_search",
            "meta": {
                "external": True,
                "server": "search",
                "transport": "stdio",
            },
        },
        {
            "name": "apply_patch",
            "meta": {
                "client_builtin": True,
                "domain": "coding",
                "class": "workspace",
            },
        },
        {
            "name": "shell_command",
            "meta": {
                "client_builtin": True,
                "domain": "coding",
                "class": "shell",
            },
        },
    ]

    render_tools_summary(
        application=application,
        tools=tools,
        terminal_width=application.viewport.width,
    )

    summary, gap = (
        call.args[0]
        for call in application.emit.call_args_list
    )
    text = "".join(value for _style, value in summary.renderable.fragments)

    assert summary.type == "tui.tools.summary"
    assert text == (
        "/tools\n\n"
        "🔌  Tools\n\n"
        f"  • {const.APP_DESC} Native\n"
        "    • Auth: N/A\n"
        "    • Transport: in-process\n"
        "    • Tools: apply_patch, shell_command\n\n"
        "  • search\n"
        "    • Auth: unknown\n"
        "    • Credentials (local): unknown\n"
        "    • Last auth request: unverified\n"
        "    • Transport: stdio\n"
        "    • Tools: external_search"
    )
    assert summary.renderable.fragments[0] == ("class:terminal.brand", "/tools")
    assert ("bold", "🔌  Tools") in summary.renderable.fragments
    assert gap.type == "tui.gap"


def test_tools_summary_uses_supplied_catalog_without_implicit_filtering() -> None:
    application = SimpleNamespace(
        emit=Mock(),
        viewport=SimpleNamespace(width=120),
    )
    tools = [
        {
            "name": "apply_patch",
            "meta": {"client_builtin": True, "domain": "coding"},
        },
        {
            "name": "plan_steps",
            "meta": {
                "client_builtin": True,
                "domain": "client",
                "class": "loop",
            },
        },
        {
            "name": "update_plan",
            "meta": {
                "client_builtin": True,
                "domain": "client",
                "class": "plan",
            },
        },
    ]

    render_tools_summary(
        application=application,
        tools=tools,
        terminal_width=application.viewport.width,
    )

    summary = application.emit.call_args_list[0].args[0]
    text = "".join(value for _style, value in summary.renderable.fragments)

    assert "🔌  Tools" in text
    assert f"  • {const.APP_DESC} Native" in text
    assert "apply_patch" in text
    assert "update_plan" in text
    assert "plan_steps" in text


def test_tools_summary_renders_empty_state_in_codex_layout() -> None:
    application = SimpleNamespace(
        emit=Mock(),
        viewport=SimpleNamespace(width=120),
    )

    render_tools_summary(
        application=application,
        tools=[],
        terminal_width=application.viewport.width,
    )

    summary = application.emit.call_args_list[0].args[0]
    text = "".join(value for _style, value in summary.renderable.fragments)

    assert text == (
        "/tools\n\n"
        "🔌  Tools\n\n"
        "  • No tools available."
    )


def test_tools_summary_wraps_tool_names_with_hanging_indent() -> None:
    application = SimpleNamespace(
        emit=Mock(),
        viewport=SimpleNamespace(width=40),
    )
    tools = [
        {"name": "browser_click", "meta": {"external": True, "server": "playwright"}},
        {"name": "browser_close", "meta": {"external": True, "server": "playwright"}},
        {"name": "browser_console_messages", "meta": {"external": True, "server": "playwright"}},
    ]

    render_tools_summary(
        application=application,
        tools=tools,
        terminal_width=application.viewport.width,
    )

    summary = application.emit.call_args_list[0].args[0]
    text = "".join(value for _style, value in summary.renderable.fragments)

    assert "    • Tools: browser_click," in text
    assert "      browser_close," in text
    assert "      browser_console_messages" in text


@pytest.mark.anyio
async def test_print_available_tools_uses_external_original_names() -> None:
    application = SimpleNamespace(
        emit=Mock(),
        viewport=SimpleNamespace(width=120),
    )
    session = SimpleNamespace(
        display_name_for_tool=lambda name: (
            "browser_click"
            if name == "mcp__playwright__browser_click"
            else name
        ),
    )
    catalog = [{
        "name": "mcp__playwright__browser_click",
        "meta": {
            "external": True,
            "server": "playwright",
            "transport": "stdio",
        },
    }]

    async def with_mcp_session(_pref_config, callback) -> None:
        await callback(session, catalog)

    host = SimpleNamespace(
        frontend=SimpleNamespace(application=application),
        execution=SimpleNamespace(
            with_mcp_session=with_mcp_session,
            external_mcp=SimpleNamespace(snapshot=McpRuntimeSnapshot("runtime", "/workspace", ()), control=AsyncMock()),
        ),
    )

    await print_available_tools(host, pref_config={})

    summary = application.emit.call_args_list[0].args[0]
    text = "".join(value for _style, value in summary.renderable.fragments)

    assert "browser_click" in text
    assert "mcp__playwright__browser_click" not in text
