# -*- coding: utf-8 -*-

import asyncio
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import (
    AsyncMock,
    Mock,
)

import pytest

from prompt_toolkit.utils import get_cwidth

from agent.ports.mcp_runtime import (
    McpAllServices,
    McpControlRequest,
    McpControlResult,
    McpRuntimeSnapshot,
    McpServiceOutcome,
    McpServiceSnapshot,
    McpServicesBusy,
    McpSingleService,
)
from frontends.tui.core.keymap import TuiRuntimeKeymap
from frontends.tui.core.menu import TuiMenu
from frontends.tui.core.models import (
    MenuDescriptionLayout,
)
from frontends.tui.core.runtime import TuiRuntime
from frontends.tui.features import mcp
from frontends.tui.session.barriers import TuiForegroundTasks
from frontends.tui.session.dispatch import (
    DispatchAction,
    TuiCommandDispatcher,
)


def _external_mcp_owner(runtime=None, **operations):
    return SimpleNamespace(current=runtime, **operations)


def _execution(owner):
    return SimpleNamespace(external_mcp=owner)


def _application(views, *, width=120):
    return SimpleNamespace(emit=views.append, viewport=SimpleNamespace(width=width))


def _service(key="all", **changes):
    return replace(McpServiceSnapshot(key, "mcp__all__", True, "stopped", "stdio"), **changes)


def _host(services=(), *, width=120, config_error=None, control=None):
    views = []
    owner = _external_mcp_owner(
        snapshot=McpRuntimeSnapshot("instance", "/workspace", tuple(services), config_error),
        control=control or AsyncMock(),
    )
    host = SimpleNamespace(
        execution=_execution(owner),
        activity=SimpleNamespace(enabled=False, stop=AsyncMock()),
        frontend=SimpleNamespace(runtime=TuiRuntime(), application=_application(views, width=width)),
    )
    return host, views


def _request(host, action="status", key="all"):
    snapshot = host.execution.external_mcp.snapshot
    return McpControlRequest(snapshot.runtime_id, snapshot.workspace, action, McpSingleService(key))


def _menu(width=80):
    keymap = TuiRuntimeKeymap.from_config({"tui": {"keymap": {"list": {"accept": "f18", "cancel": "f19"}}}})
    menu = TuiMenu(
        invalidate=lambda: None, focus_menu=lambda: None, focus_input=lambda: None,
        get_width=lambda: width, keymap=keymap.list,
    )
    return menu, SimpleNamespace(select_menu=menu.request, push_menu=menu.push)


def _press(menu, key):
    assert menu.handle_key_event(SimpleNamespace(key=key, data="", key_sequence=(SimpleNamespace(key=key),)))


@pytest.mark.anyio
@pytest.mark.parametrize("width", [32, 80, 120])
async def test_service_menu_navigation_preserves_raw_key_selection_scroll_and_safe_default(width):
    services = [_service(f"service-{i:02}") for i in range(20)] + [_service("all")]
    host, _ = _host(services)
    menu, runtime = _menu(width)
    task = asyncio.create_task(mcp.choose_mcp_action(runtime, host))
    await asyncio.sleep(0)
    root = menu.state
    assert [option.value.config_key for option in root.request.options] == sorted(item.config_key for item in services)
    assert all(isinstance(option.value, McpSingleService) for option in root.request.options)
    menu._move(17)
    selected, scroll_top = root.selected, root.scroll_top
    assert scroll_top > 0
    _press(menu, "f18")
    child = menu.state
    assert child.request.selected == 4
    assert [option.label for option in child.request.options] == ["start", "force", "stop", "restart", "status"]
    assert child.request.description_layout is MenuDescriptionLayout.STACK_BELOW_WHEN_NARROW
    assert [item.description for item in child.request.footer_hint.commands] == ["to confirm", "to go back"]
    assert "close its child processes" in child.request.options[2].detail
    assert "only if enabled" in child.request.options[3].detail
    assert "f18" in "".join(text for _, text in menu.footer_fragments())
    assert "f19" in "".join(text for _, text in menu.footer_fragments())
    assert all(get_cwidth(line) <= width for line in "".join(text for _, text in menu.fragments()).splitlines())
    _press(menu, "f19")
    assert menu.state is root
    assert (root.selected, root.scroll_top) == (selected, scroll_top)
    menu._choose_index(0)
    _press(menu, "f18")
    result = await task
    assert result == _request(host)
    assert not menu.active
    host.execution.external_mcp.control.assert_not_awaited()


@pytest.mark.anyio
@pytest.mark.parametrize("config_error", [None, "invalid config"])
@pytest.mark.parametrize("key", ["f18", "f19"])
async def test_empty_menu_closes_without_control_request(config_error, key):
    host, _ = _host(config_error=config_error)
    menu, runtime = _menu()
    task = asyncio.create_task(mcp.choose_mcp_action(runtime, host))
    await asyncio.sleep(0)
    assert menu.state.request.options == ()
    assert menu.state.request.footer_hint.commands[0].description == "to close"
    assert menu.state.request.body.count("invalid config") == int(config_error is not None)
    _press(menu, key)
    assert await task is None
    host.execution.external_mcp.control.assert_not_awaited()


@pytest.mark.anyio
@pytest.mark.parametrize("transport", ["streamable_http", "sse"])
async def test_remote_service_menu_stop_explains_remote_stays_running(transport):
    host, _ = _host([_service(transport=transport, config_enabled=False, state="ready")])
    menu, runtime = _menu()
    task = asyncio.create_task(mcp.choose_mcp_action(runtime, host))
    await asyncio.sleep(0)
    _press(menu, "f18")
    assert "temporary connection" in menu.state.request.status
    assert menu.state.request.options[2].detail == "Disconnect this service; the remote server keeps running."
    _press(menu, "f19")
    _press(menu, "f19")
    assert await task is None


@pytest.mark.parametrize(("command", "expected"), [
    ("/mcp", (True, None)), ("/MCP START", (True, "start")),
    ("/mcp force", (True, "force")), ("/mcp restart", (True, "restart")),
    ("hello", (False, None)),
])
def test_parse_mcp_command(command, expected):
    assert mcp.parse_mcp_command(command) == expected


@pytest.mark.anyio
@pytest.mark.parametrize("command", ["/mcp unknown", "/mcp stop extra", "/mcp status all", "/mcp force all"])
async def test_invalid_mcp_command_is_local_without_model_or_control(command):
    with pytest.raises(ValueError, match="Usage:"):
        mcp.parse_mcp_command(command)
    host, views = _host()
    dispatcher = TuiCommandDispatcher(host, SimpleNamespace(), SimpleNamespace(), SimpleNamespace(), protocol_client=Mock())
    assert await dispatcher.dispatch(command) is DispatchAction.HANDLED
    assert views[-1].renderable.fragments
    host.execution.external_mcp.control.assert_not_awaited()


@pytest.mark.anyio
@pytest.mark.parametrize("action", ["start", "force", "stop", "restart", "status"])
async def test_direct_request_is_explicit_all_and_menu_key_all_stays_single(action):
    host, _ = _host([_service()])
    for command in (mcp.all_mcp_request(host, action), _request(host, action)):
        await mcp.run_mcp_action(host, command)
        host.execution.external_mcp.control.assert_awaited_with(command, defer_activity_stop=True)
    assert isinstance(mcp.all_mcp_request(host, action).target, McpAllServices)
    assert _request(host, action).target == McpSingleService("all")


@pytest.mark.anyio
@pytest.mark.parametrize("action", ["start", "force"])
async def test_stream_incremental_start_is_allowed_with_existing_connections(action):
    host, _ = _host([_service(state="ready")])
    host.execution.external_mcp.current = SimpleNamespace(started=True)
    tasks = TuiForegroundTasks(host.frontend.runtime, host)
    tasks.start_external_mcp = Mock(return_value=True)
    assert tasks.handle_stream_command(f"/mcp {action}", lambda: False)
    tasks.start_external_mcp.assert_called_once_with(mcp.all_mcp_request(host, action))


@pytest.mark.parametrize("width", [32, 60, 120])
def test_status_separates_connection_config_and_wraps_long_names(width):
    services = (
        _service("全长名称" * 15, state="ready", tools=("mcp__all__" + "unbroken" * 30,), discovered=4, filtered=3),
        _service("temporary", state="ready", config_enabled=False),
        _service("removed", state="ready", config_enabled=None),
        _service("failed", state="failed", connection_error="connection lost"),
    )
    host, views = _host(services, width=width, config_error="invalid config")
    mcp.render_mcp_status(host)
    assert [view.type for view in views] == ["tui.mcp", "tui.gap"]
    text = "".join(value for _, value in views[0].renderable.fragments)
    compact = text.replace("\n", "")
    for label in ("Connection: ready", "Tools (0): (none)", "disabled (temporary connection)", "removed from config", "Connection: failed", "Config error: invalid config", "Filtered: 3"):
        assert label in compact
    assert all(get_cwidth(line) <= width for line in text.splitlines())
    host.execution.external_mcp.control.assert_not_awaited()


def test_status_rejects_stale_identity_and_unknown_target():
    host, views = _host([_service("other")])
    for command in (_request(host), replace(_request(host), runtime_id="old")):
        mcp.render_mcp_status(host, command)
    statuses = [view.renderable.plain_text for view in views if view.type == "tui.external_mcp.status"]
    assert len(statuses) == 2
    assert "no longer exists" in statuses[0]
    assert "no longer active" in statuses[1]


@pytest.mark.parametrize("outcome", ["applied", "unchanged", "disabled", "busy", "failed"])
def test_action_result_has_scope_and_exactly_one_final_block(outcome):
    host, views = _host()
    result = McpControlResult(_request(host, "force"), (
        McpServiceOutcome("all", outcome, _service(state="ready", config_enabled=False)),
    ))
    mcp.render_mcp_action_result(host, result)
    assert [view.type for view in views] == ["tui.external_mcp.status", "tui.gap"]
    text = views[0].renderable.plain_text
    assert "External MCP · all · force" in text
    assert f"all: {outcome}" in text
    assert "ready" in text and "temporary connection" in text


@pytest.mark.anyio
@pytest.mark.parametrize("disposition", ["success", "failure", "busy", "cancel"])
async def test_foreground_activity_handoff_has_one_scoped_final_result(disposition):
    started = asyncio.Event()
    host, views = _host([_service()])
    command = _request(host, "stop")
    result = McpControlResult(command, (McpServiceOutcome("all", "applied", _service()),))

    async def control(_request, **_kwargs):
        started.set()
        if disposition == "cancel":
            await asyncio.Future()
        if disposition == "failure":
            raise RuntimeError("cleanup failed")
        if disposition == "busy":
            raise McpServicesBusy(("all",))
        return result

    host.execution.external_mcp.control = control
    tasks = TuiForegroundTasks(host.frontend.runtime, host)
    tasks.start_external_mcp(command)
    await started.wait()
    if disposition == "cancel":
        tasks.cancel()
    await tasks.wait()
    finals = [view for view in views if view.type != "tui.gap"]
    assert len(finals) == 1
    text = (
        "".join(value for _, value in finals[0].renderable.fragments)
        if disposition == "cancel" else finals[0].renderable.plain_text
    )
    assert "External MCP · all · stop" in text
    assert ("interrupted" in text) == (disposition == "cancel")
    assert not host.frontend.runtime.foreground_active


@pytest.mark.parametrize(
    ("snapshot", "expected"),
    [
        (
            {
                "done": True,
                "items": [
                    {
                        "name": "docs",
                        "state": "ready",
                        "tools": 4,
                        "discovered": 7,
                        "filtered": 3,
                    },
                ],
            },
            "■ External MCP ready · 1/1 servers · 4 tools · 3 filtered",
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
            "■ External MCP failed · 0/1 servers\n  └ docs: timeout",
        ),
    ],
)
def test_external_mcp_start_result_is_committed_to_tui(
    snapshot,
    expected,
) -> None:
    views = []
    host = SimpleNamespace(
        execution=_execution(_external_mcp_owner(
            SimpleNamespace(last_start_snapshot=snapshot),
        )),
        frontend=SimpleNamespace(
            application=_application(views),
        ),
    )

    assert mcp.render_external_mcp_start_status(host)

    status = next(
        view for view in views
        if view.type == "tui.external_mcp.status"
    )
    assert status.renderable.plain_text == expected


def test_partial_external_mcp_failure_is_not_bold() -> None:
    snapshot = {
        "done": True,
        "items": [
            {"name": "github", "state": "ready", "tools": 7},
            {
                "name": "docs",
                "state": "failed",
                "tools": 0,
                "detail": "timeout",
            },
        ],
    }
    views = []
    host = SimpleNamespace(
        execution=_execution(_external_mcp_owner(
            SimpleNamespace(last_start_snapshot=snapshot),
        )),
        frontend=SimpleNamespace(
            application=_application(views),
        ),
    )

    assert mcp.render_external_mcp_start_status(host)

    status = next(
        view for view in views
        if view.type == "tui.external_mcp.status"
    )
    assert status.renderable.plain_text == (
        "■ External MCP ready · 1/2 servers · 7 tools\n"
        "  └ docs: timeout"
    )
    assert all(not span.style.bold for span in status.renderable.spans)
