# -*- coding: utf-8 -*-

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from engine.errors import AppError
from mind_app.tui.core.runtime import TuiRuntime
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


@pytest.mark.anyio
async def test_mcp_menu_does_not_duplicate_invalid_config_marker(monkeypatch) -> None:
    runtime = _Runtime()
    monkeypatch.setattr(
        mcp,
        "summarize_external_runtime",
        lambda _mind: {
            "started": False,
            "configured": [],
            "config_error": "invalid config",
            "tool_groups": [],
            "tool_count": 0,
        },
    )

    await mcp.choose_mcp_action(runtime, object())

    assert runtime.request.status == "config=invalid"
    assert runtime.request.body == ("invalid config",)


def test_mcp_status_uses_discovered_and_exposed_tool_counts(tmp_path) -> None:
    tool_meta = {"server": "zentao", "transport": "stdio"}
    group = SimpleNamespace(
        tools={
            "mcp__zentao__get_bug": SimpleNamespace(meta=tool_meta),
            "mcp__zentao__list_bug": SimpleNamespace(meta=tool_meta),
        },
        server_stats={
            "zentao": {
                "server": "zentao",
                "transport": "stdio",
                "discovered": 5,
                "exposed": 2,
                "filtered": 3,
            },
        },
    )
    mind = SimpleNamespace(
        src_opera_place=tmp_path,
        config_session=SimpleNamespace(load=lambda: {
            "mcp_servers": {
                "zentao": {"command": "zentao-server"},
            },
        }),
        external_mcp=SimpleNamespace(started=True, group=group),
    )

    summary = mcp.summarize_external_runtime(mind)

    assert summary["tool_count"] == 2
    assert summary["filtered_count"] == 3
    assert summary["tool_groups"] == [{
        "server": "zentao",
        "transport": "stdio",
        "tools": ["mcp__zentao__get_bug", "mcp__zentao__list_bug"],
        "discovered": 5,
        "exposed": 2,
        "filtered": 3,
    }]


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
async def test_force_uses_start_when_runtime_is_not_running(monkeypatch) -> None:
    mind = SimpleNamespace(
        external_mcp=None,
        start_external_mcp_runtime=AsyncMock(),
        restart_external_mcp_runtime=AsyncMock(),
    )
    await mcp.run_mcp_action(mind, "force")

    mind.start_external_mcp_runtime.assert_awaited_once_with(
        include_disabled=True,
        defer_activity_stop=True,
    )
    mind.restart_external_mcp_runtime.assert_not_awaited()


@pytest.mark.anyio
async def test_mcp_cancellation_is_rendered_as_interrupted() -> None:
    views = []
    started = asyncio.Event()

    async def start_runtime(**_kwargs) -> None:
        started.set()
        await asyncio.Future()

    mind = SimpleNamespace(
        external_mcp=None,
        start_external_mcp_runtime=start_runtime,
        frontend=SimpleNamespace(
            application=SimpleNamespace(emit=views.append),
        ),
    )
    task = asyncio.create_task(mcp.run_mcp_action(mind, "start"))
    await started.wait()
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task

    mcp.render_mcp_action_cancelled(mind, "start")

    status = next(
        view for view in views
        if view.type == "tui.external_mcp.interrupted"
    )
    assert "".join(
        text for _style, text in status.renderable.fragments
    ) == "External MCP · start interrupted"


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("started", "expected"),
    [
        (True, "■ External MCP stopped"),
        (False, "■ External MCP already stopped"),
    ],
)
async def test_mcp_stop_commits_compact_final_status(started, expected) -> None:
    views = []
    mind = SimpleNamespace(
        external_mcp=(SimpleNamespace(started=True) if started else None),
        stop_external_mcp_runtime=AsyncMock(),
        frontend=SimpleNamespace(
            runtime=TuiRuntime(),
            application=SimpleNamespace(emit=views.append),
        ),
    )

    was_started = await mcp.run_mcp_action(mind, "stop")
    await mcp.finish_mcp_activity(mind, "stop")
    mcp.render_mcp_action_result(mind, "stop", was_started)

    status = next(
        view for view in views
        if view.type == "tui.external_mcp.status"
    )
    assert status.renderable.plain_text == expected
    assert not any(
        view.type == "tui.external_mcp.interrupted"
        for view in views
    )


@pytest.mark.anyio
async def test_mcp_stop_failure_has_stop_specific_status() -> None:
    views = []
    mind = SimpleNamespace(
        external_mcp=SimpleNamespace(started=True),
        stop_external_mcp_runtime=AsyncMock(
            side_effect=AppError("cleanup failed"),
        ),
        frontend=SimpleNamespace(
            runtime=TuiRuntime(),
            application=SimpleNamespace(emit=views.append),
        ),
    )

    with pytest.raises(AppError) as captured:
        await mcp.run_mcp_action(mind, "stop")
    await mcp.finish_mcp_activity(mind, "stop")
    mcp.render_mcp_action_failure(mind, "stop", captured.value)

    status = next(
        view for view in views
        if view.type == "tui.external_mcp.status"
    )
    assert status.renderable.plain_text == (
        "■ External MCP stop failed\n└ cleanup failed"
    )


@pytest.mark.anyio
async def test_completed_mcp_stop_is_not_reported_as_interrupted() -> None:
    views = []
    cleanup_started = asyncio.Event()
    release_cleanup = asyncio.Event()
    cleanup_finished = asyncio.Event()

    async def cleanup() -> None:
        cleanup_started.set()
        await release_cleanup.wait()
        cleanup_finished.set()

    async def stop_runtime() -> None:
        mind.external_mcp = None
        cleanup_task = asyncio.create_task(cleanup())
        try:
            await asyncio.shield(cleanup_task)
        except asyncio.CancelledError:
            await cleanup_task
            raise

    mind = SimpleNamespace(
        external_mcp=SimpleNamespace(started=True),
        stop_external_mcp_runtime=stop_runtime,
        frontend=SimpleNamespace(
            runtime=TuiRuntime(),
            application=SimpleNamespace(emit=views.append),
        ),
    )
    task = asyncio.create_task(mcp.run_mcp_action(mind, "stop"))
    await cleanup_started.wait()
    task.cancel()
    assert not task.done()
    release_cleanup.set()

    with pytest.raises(asyncio.CancelledError):
        await task

    await mcp.finish_mcp_activity(mind, "stop")
    mcp.render_mcp_action_cancelled(mind, "stop")

    assert cleanup_finished.is_set()
    status = next(
        view for view in views
        if view.type == "tui.external_mcp.status"
    )
    assert status.renderable.plain_text == "■ External MCP stopped"
    assert not any(
        view.type == "tui.external_mcp.interrupted"
        for view in views
    )


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


def test_mcp_force_result_keeps_activity_prefix() -> None:
    views = []
    mind = SimpleNamespace(
        external_mcp=SimpleNamespace(last_start_snapshot={
            "done": True,
            "items": [{
                "name": "docs",
                "state": "ready",
                "tools": 4,
                "discovered": 4,
                "filtered": 0,
            }],
        }),
        frontend=SimpleNamespace(
            application=SimpleNamespace(emit=views.append),
        ),
    )

    mcp.render_mcp_action_result(mind, "force", was_started=False)

    status = next(
        view for view in views
        if view.type == "tui.external_mcp.status"
    )
    assert status.renderable.plain_text == (
        "■ External MCP ready · 1/1 servers · 4 tools"
    )


def test_external_mcp_status_is_one_compact_block(monkeypatch) -> None:
    views = []
    mind = SimpleNamespace(
        frontend=SimpleNamespace(
            application=SimpleNamespace(emit=views.append),
        ),
    )
    monkeypatch.setattr(
        mcp,
        "summarize_external_runtime",
        lambda _mind: {
            "started": False,
            "configured": [
                {"name": "playwright", "transport": "stdio", "enabled": False},
                {"name": "docs", "transport": "streamable_http", "enabled": True},
            ],
            "config_error": "",
            "tool_groups": [],
            "tool_count": 0,
            "filtered_count": 0,
        },
    )

    mcp.render_mcp_status(mind)

    assert [view.type for view in views] == ["tui.mcp", "tui.gap"]
    assert views[0].renderable.fragments
    assert "".join(
        text for _style, text in views[0].renderable.fragments
    ) == (
        "/mcp status · started=false configured=2 tools=0 filtered=0\n"
        "Configured servers\n"
        "  • playwright (stdio · disabled)\n"
        "  • docs (streamable_http · enabled)\n"
        "No external MCP servers connected."
    )
