# -*- coding: utf-8 -*-

import sys
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from mind_app.native_coding import NativeCoding
from mind_app.tui.core.process_viewer import (
    ProcessViewerRequest,
    TuiProcessViewer,
)
from mind_app.tui.core.models import FragmentBlock
from mind_app.tui.core.runtime import TuiRuntime
from mind_app.tui.features.shell import run_shell_escape
from mind_app.tui.features.processes import watch_exec_session


class _ApplicationStub(object):
    def __init__(self) -> None:
        self.viewport = SimpleNamespace(width=80, height=24)
        self.views = []

    def emit(self, view) -> None:
        self.views.append(view)


@pytest.mark.anyio
async def test_shell_escape_starts_shared_session_and_attaches_viewer() -> None:
    application = _ApplicationStub()
    native_coding = SimpleNamespace(
        start_user_shell_session=AsyncMock(return_value={
            "ok": True,
            "session_id": "exec_shell",
            "origin": "tui_shell",
        }),
    )
    mind = SimpleNamespace(
        frontend=SimpleNamespace(application=application),
        native_coding=native_coding,
    )
    runtime = object()

    with (
        patch(
            "mind_app.tui.features.shell.default_shell_executable",
            return_value="shell",
        ),
        patch(
            "mind_app.tui.features.shell.direct_command_args",
            return_value=["resolved", "arg"],
        ),
        patch(
            "mind_app.tui.features.shell.watch_exec_session",
            new=AsyncMock(return_value=True),
        ) as watch,
    ):
        handled = await run_shell_escape(runtime, mind, "!resolved arg")

    assert handled
    native_coding.start_user_shell_session.assert_awaited_once_with(
        command="resolved arg",
        args=["resolved", "arg"],
        timeout_sec=3600,
    )
    watch.assert_awaited_once_with(
        runtime,
        mind,
        "exec_shell",
        announce_detach=True,
    )


@pytest.mark.anyio
async def test_user_shell_session_is_listed_and_closed(tmp_path) -> None:
    coding = NativeCoding(root=tmp_path)
    snapshot = await coding.start_user_shell_session(
        command="background test",
        args=[
            sys.executable,
            "-c",
            "import time; print('ready', flush=True); time.sleep(30)",
        ],
        timeout_sec=60,
        idle_timeout_sec=60,
    )

    try:
        session_id = snapshot["session_id"]
        for _index in range(20):
            snapshot = await coding.exec_session_output_snapshot(
                session_id=session_id,
            )
            if "ready" in snapshot.get("output", ""):
                break
            await asyncio.sleep(0.05)

        running = await coding.running_exec_sessions()
        item = next(
            item for item in running["items"]
            if item["session_id"] == session_id
        )

        assert item["origin"] == "tui_shell"
        assert "ready" in snapshot["output"]
    finally:
        await coding.close()

    assert (await coding.running_exec_sessions())["count"] == 0


@pytest.mark.anyio
async def test_exec_command_keeps_tool_policy_and_result_flow(tmp_path) -> None:
    coding = NativeCoding(root=tmp_path)
    try:
        result = await coding.exec_command(
            command="echo shared-session",
            yield_time_ms=2000,
            timeout_sec=30,
            execution={
                "grantId": "test-shared-session",
                "state": "approved",
                "target": "local",
            },
        )
    finally:
        await coding.close()

    assert result["ok"]
    assert result["data"]["status"] == "exited"
    assert "shared-session" in result["data"]["output"]


@pytest.mark.anyio
async def test_process_viewer_returns_detach_without_using_input_buffer() -> None:
    focused = []
    viewer = TuiProcessViewer(
        invalidate=lambda: None,
        focus_viewer=lambda: focused.append("viewer"),
        focus_input=lambda: focused.append("input"),
        get_width=lambda: 80,
    )
    task = asyncio.create_task(viewer.request(ProcessViewerRequest(
        fragments=(("class:ps.title", "Shell running"),),
    )))
    await asyncio.sleep(0)

    viewer.finish("detach")

    assert await task == "detach"
    assert focused == ["viewer", "input"]
    assert not viewer.active


@pytest.mark.anyio
async def test_detaching_shell_viewer_keeps_session_for_ps() -> None:
    application = _ApplicationStub()
    snapshot = {
        "ok": True,
        "session_id": "exec_background",
        "command": "long task",
        "status": "running",
        "origin": "tui_shell",
        "output_lines": ["ready"],
    }
    native_coding = SimpleNamespace(
        exec_session_output_snapshot=AsyncMock(return_value=snapshot),
    )
    mind = SimpleNamespace(
        frontend=SimpleNamespace(application=application),
        native_coding=native_coding,
    )
    runtime = SimpleNamespace(
        view_process=AsyncMock(return_value="detach"),
        update_process_viewer=lambda request: None,
        finish_process_viewer=lambda value: None,
        cancel_background_session_task=lambda session_id: None,
        start_background_session_task=(
            lambda session_id, awaitable: awaitable.close()
        ),
    )

    viewed = await watch_exec_session(
        runtime,
        mind,
        "exec_background",
        announce_detach=True,
    )

    assert viewed
    assert native_coding.exec_session_output_snapshot.await_count >= 1
    summary = application.views[-1]
    text = "".join(value for _style, value in summary.renderable.fragments)
    assert summary.type == "tui.command_summary"
    assert "Shell" in text
    assert "background" in text
    assert "exec_background" in text


def test_background_completion_waits_for_stream_boundary() -> None:
    runtime = TuiRuntime()
    block = FragmentBlock((("class:ps.title", "Shell completed"),))
    runtime.set_execution_active(True)

    runtime.queue_background_block(block)

    assert not runtime.document.blocks
    assert runtime._background_blocks == [block]

    runtime.set_execution_active(False)

    assert runtime.document.blocks[-1].block == block
    assert not runtime._background_blocks


@pytest.mark.anyio
async def test_runtime_process_viewer_replaces_input_area() -> None:
    runtime = TuiRuntime()
    task = asyncio.create_task(runtime.view_process(ProcessViewerRequest(
        fragments=(("class:ps.title", "Shell running\noutput"),),
    )))
    await asyncio.sleep(0)

    assert runtime.process_viewer.active
    assert runtime._process_viewer_height() == 2
    assert runtime._interaction_height() == 0
    assert not runtime.input_area.filter()

    runtime.finish_process_viewer("detach")
    assert await task == "detach"
    assert runtime.input_area.filter()
