# -*- coding: utf-8 -*-

import sys
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from prompt_toolkit.utils import get_cwidth

from mind_app.native_coding import NativeCoding
from mind_app.native_coding.exec.process_session import ProcessSessionManager
from mind_app.tui.core.process_viewer import (
    ProcessViewerRequest,
    TuiProcessViewer,
)
from mind_app.tui.core.models import FragmentBlock
from mind_app.tui.core.runtime import TuiRuntime
from mind_app.tui.features.shell import run_shell_escape
from mind_app.tui.features.processes import (
    PROCESS_VIEWER_FOCUS_REQUEST,
    append_exec_stream_snapshot,
    exec_session_live_block,
    exec_session_summary_block,
    manage_exec_sessions,
    render_exec_session_panel,
    render_exec_sessions_stopped,
    watch_exec_session,
)


class _ApplicationStub(object):
    def __init__(self) -> None:
        self.viewport = SimpleNamespace(width=80, height=24)
        self.views = []

    def emit(self, view) -> None:
        self.views.append(view)


def test_empty_shell_mode_submission_is_silent() -> None:
    runtime = TuiRuntime()
    runtime.input_model.set_shell_mode(True)

    handled = runtime.submissions.accept_input(runtime.screen.input.buffer)

    assert not handled
    assert runtime.submissions.message_queue.empty()
    assert runtime.input_model.shell_mode
    assert not runtime.document.blocks


@pytest.mark.anyio
async def test_ps_without_sessions_renders_command_and_empty_terminal_state() -> None:
    application = _ApplicationStub()
    native_coding = SimpleNamespace(
        running_exec_sessions=AsyncMock(return_value={
            "count": 0,
            "items": [],
        }),
    )
    runtime = SimpleNamespace()
    mind = SimpleNamespace(
        frontend=SimpleNamespace(application=application),
        native_coding=native_coding,
    )

    handled = await manage_exec_sessions(runtime, mind)

    assert handled
    fragments = application.views[-1].renderable.fragments
    assert "".join(text for _style, text in fragments) == (
        "/ps\n\n"
        "Background terminals\n\n"
        "  • No background terminals running."
    )
    assert fragments[0] == ("class:prompt.command.slash", "/ps")


@pytest.mark.anyio
async def test_streaming_ps_appends_dimmed_process_summaries_without_menu() -> None:
    runtime = TuiRuntime()
    runtime.screen._output_size = lambda: (80, 24)
    runtime.set_active_renderable(
        FragmentBlock((("class:assistant", "model stream"),)),
        kind="assistant",
    )
    sessions = [
        {
            "session_id": f"exec_{index}",
            "command": command,
            "pid": 100 + index,
            "started_at": float(index),
            "origin": "tool",
        }
        for index, command in enumerate([
            "adb logcat",
            "npm run dev",
            "pytest -q",
            "hidden command",
        ])
    ]

    async def snapshot_for_session(
        *,
        session_id: str,
        max_output_chars: int,
    ) -> dict[str, object]:
        index = int(session_id.removeprefix("exec_"))
        return {
            **sessions[index],
            "ok": True,
            "status": "running",
            "output_lines": [f"process {index} line {line}" for line in range(5)],
        }

    output_snapshot = AsyncMock(side_effect=snapshot_for_session)
    mind = SimpleNamespace(native_coding=SimpleNamespace(
        running_exec_sessions=AsyncMock(return_value={
            "count": len(sessions),
            "items": sessions,
        }),
        exec_session_output_snapshot=output_snapshot,
    ))

    await append_exec_stream_snapshot(runtime, mind)

    assert output_snapshot.await_count == 3
    for index in range(3):
        output_snapshot.assert_any_await(
            session_id=f"exec_{index}",
            max_output_chars=60000,
        )
    assert runtime.document.active_kind == "assistant"
    fragments = runtime.document.fragments(width=80)
    text = "".join(value for _style, value in fragments)
    assert "model stream\n\n/ps\n\nBackground terminals" in text
    assert "  • adb logcat\n    ↳ process 0 line 2" in text
    assert "      process 0 line 3\n      process 0 line 4" in text
    assert "  • npm run dev\n    ↳ process 1 line 2" in text
    assert "  • pytest -q\n    ↳ process 2 line 2" in text
    assert "process 0 line 1" not in text
    assert "hidden command" not in text
    assert text.endswith("  … and 1 more running")
    assert "Enter/Esc/q" not in text
    stream_text = "".join(
        value
        for style, value in fragments
        if style == "class:ps.stream"
    )
    command_text = [
        value
        for style, value in fragments
        if style == "class:ps.stream.command"
    ]
    assert stream_text.startswith("  • ")
    assert stream_text.endswith("  … and 1 more running")
    assert command_text == ["adb logcat", "npm run dev", "pytest -q"]


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
async def test_process_session_manager_stops_all_running_sessions() -> None:
    manager = ProcessSessionManager()
    sessions = [
        SimpleNamespace(
            session_id=f"exec_{index}",
            command=f"command {index}",
            process=SimpleNamespace(pid=100 + index, returncode=None),
            origin="tui_shell" if index == 1 else "tool",
        )
        for index in range(1, 3)
    ]
    manager.sessions = {
        session.session_id: session
        for session in sessions
    }
    manager.cleanup = AsyncMock()
    manager.finalize_if_exited = AsyncMock()

    async def terminate(process, *, force) -> None:
        assert not force
        process.returncode = -15

    with patch(
        "mind_app.native_coding.exec.process_session."
        "ProcessCapture.terminate_process_tree",
        side_effect=terminate,
    ):
        result = await manager.stop_running_sessions()

    assert result == {
        "ok": True,
        "requested": 2,
        "stopped": 2,
        "failed": 0,
        "items": [
            {
                "session_id": "exec_1",
                "command": "command 1",
                "pid": 101,
                "origin": "tui_shell",
                "exit_code": -15,
            },
            {
                "session_id": "exec_2",
                "command": "command 2",
                "pid": 102,
                "origin": "tool",
                "exit_code": -15,
            },
        ],
        "failures": [],
    }
    assert not manager.sessions
    assert manager.finalize_if_exited.await_count == 2

    empty_result = await manager.stop_running_sessions()

    assert empty_result == {
        "ok": True,
        "requested": 0,
        "stopped": 0,
        "failed": 0,
        "items": [],
        "failures": [],
    }


@pytest.mark.anyio
async def test_ps_stop_all_confirms_and_cancels_background_watchers() -> None:
    application = _ApplicationStub()
    snapshot = {
        "count": 2,
        "items": [
            {
                "session_id": "exec_shell",
                "command": "shell task",
                "pid": 101,
                "origin": "tui_shell",
            },
            {
                "session_id": "exec_tool",
                "command": "tool task",
                "pid": 102,
                "origin": "tool",
            },
        ],
    }
    stopped = {
        "ok": True,
        "requested": 2,
        "stopped": 2,
        "failed": 0,
        "items": snapshot["items"],
        "failures": [],
    }
    native_coding = SimpleNamespace(
        running_exec_sessions=AsyncMock(side_effect=[snapshot, snapshot]),
        stop_exec_sessions=AsyncMock(return_value=stopped),
    )
    requests = []
    cancelled = []
    status_labels = []

    async def select_menu(request):
        requests.append(request)
        if len(requests) == 1:
            return request.options[-1].value
        return request.options[1].value

    runtime = SimpleNamespace(
        select_menu=select_menu,
        cancel_background_session_task=cancelled.append,
        set_process_status_label=status_labels.append,
    )
    mind = SimpleNamespace(
        frontend=SimpleNamespace(application=application),
        native_coding=native_coding,
    )

    handled = await manage_exec_sessions(runtime, mind)

    assert handled
    assert [request.title for request in requests] == [
        "Background Commands",
        "Stop Background Commands",
    ]
    assert requests[1].selected == 0
    assert requests[1].options[0].value is False
    assert cancelled == ["exec_shell", "exec_tool"]
    assert status_labels == [""]
    native_coding.running_exec_sessions.assert_awaited_once_with()
    native_coding.stop_exec_sessions.assert_awaited_once_with()
    text = "".join(
        value
        for _style, value in application.views[-1].renderable.fragments
    )
    assert "stop all background commands" in text
    assert "stopped=2" in text
    assert "\n  └ requested=2 · stopped=2 · failed=0" in text


@pytest.mark.anyio
async def test_ps_stop_all_defaults_to_cancel() -> None:
    application = _ApplicationStub()
    snapshot = {
        "count": 1,
        "items": [{
            "session_id": "exec_shell",
            "command": "shell task",
            "pid": 101,
            "origin": "tui_shell",
        }],
    }
    native_coding = SimpleNamespace(
        running_exec_sessions=AsyncMock(side_effect=[snapshot, snapshot]),
        stop_exec_sessions=AsyncMock(),
    )
    requests = []

    async def select_menu(request):
        requests.append(request)
        return request.options[-1].value if len(requests) == 1 else None

    runtime = SimpleNamespace(select_menu=select_menu)
    mind = SimpleNamespace(
        frontend=SimpleNamespace(application=application),
        native_coding=native_coding,
    )

    handled = await manage_exec_sessions(runtime, mind)

    assert not handled
    assert requests[1].selected == 0
    native_coding.stop_exec_sessions.assert_not_awaited()


@pytest.mark.anyio
async def test_ps_selection_activates_viewer_before_loading_output() -> None:
    application = _ApplicationStub()
    output_requested = asyncio.Event()
    release_output = asyncio.Event()
    session = {
        "session_id": "exec_shell",
        "command": "long task",
        "pid": 101,
        "origin": "tui_shell",
    }

    async def output_snapshot(**_kwargs):
        output_requested.set()
        await release_output.wait()
        return {**session, "ok": True, "status": "running", "output_lines": []}

    runtime = TuiRuntime()
    mind = SimpleNamespace(
        frontend=SimpleNamespace(application=application),
        native_coding=SimpleNamespace(
            running_exec_sessions=AsyncMock(return_value={
                "count": 1,
                "items": [session],
            }),
            exec_session_output_snapshot=output_snapshot,
        ),
    )

    task = asyncio.create_task(manage_exec_sessions(runtime, mind))
    while not runtime.screen.menu.active:
        await asyncio.sleep(0)

    runtime.finish_menu("exec_shell")
    await output_requested.wait()

    assert runtime.screen.process_viewer.active
    assert runtime.screen.bottom_pane.active_surface == "process_viewer"
    assert not runtime.screen.input_area.filter()

    runtime.resolve_process_viewer("detach")
    release_output.set()
    assert await task


def test_ps_stop_all_partial_result_uses_tree_branches() -> None:
    application = _ApplicationStub()

    render_exec_sessions_stopped(application, {
        "requested": 2,
        "stopped": 1,
        "failed": 1,
        "failures": [{
            "pid": 102,
            "command": "adb logcat",
            "reason": "access_denied",
        }],
    })

    text = "".join(
        value
        for _style, value in application.views[-1].renderable.fragments
    )
    assert "\n  ├ requested=2 · stopped=1 · failed=1" in text
    assert "\n  └ failed pid=102 adb logcat · access_denied" in text


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

    viewer.resolve("detach")

    assert await task == "detach"
    assert focused == ["viewer"]
    assert viewer.active

    viewer.settle()

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
    committed = []
    runtime = SimpleNamespace(
        view_process=AsyncMock(return_value="detach"),
        update_process_viewer=lambda block: None,
        resolve_process_viewer=lambda value: None,
        commit_process_viewer=committed.append,
        dismiss_process_viewer=lambda: None,
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
    assert len(committed) == 1
    text = "".join(value for _style, value in committed[0].fragments)
    assert "Shell" in text
    assert " · background · " not in text
    assert "exec_background" in text
    assert "ready" in text


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


def test_process_stream_panel_uses_two_clipped_header_lines() -> None:
    fragments = render_exec_session_panel(
        {"snapshot": {
            "ok": True,
            "session_id": "exec_shell",
            "command": f"adb logcat {'中' * 80}",
            "status": "running",
            "pid": 123,
            "origin": "tool",
            "truncated": True,
            "output_lines": [f"line {index}" for index in range(12)],
        }},
        height=10,
        terminal_width=60,
    )
    lines = "".join(text for _style, text in fragments).splitlines()

    first_line = []
    for style, text in fragments:
        if "\n" in text:
            break
        first_line.append((style, text))

    assert lines[0].startswith("Exec running · pid=123 · exec_shell")
    assert lines[0].endswith("…")
    assert lines[1].startswith("Enter/Esc/q background")
    assert lines[2:] == [f"  line {index}" for index in range(4, 12)]
    assert [style for style, _text in first_line] == [
        "class:shell.title.action",
        "class:ps.meta",
        "class:ps.separator",
        "class:ps.meta",
        "class:ps.separator",
        "class:ps.meta",
        "class:ps.separator",
        "class:ps.warning",
        "class:ps.separator",
        "class:ps.command",
    ]
    assert first_line[-1][1].endswith("…")
    assert all(
        style == "class:ps.output"
        for style, text in fragments
        if text.startswith("line ")
    )
    assert all(get_cwidth(line) <= 60 for line in lines)


def test_foreground_completion_commits_before_barrier_release() -> None:
    runtime = TuiRuntime()
    block = FragmentBlock((("class:ps.title", "Operation ready"),))
    runtime.set_foreground_active(True)

    runtime.queue_background_block(block)

    assert runtime.document.blocks[-1].block == block
    assert not runtime._background_blocks


@pytest.mark.anyio
async def test_runtime_process_viewer_replaces_input_area() -> None:
    runtime = TuiRuntime()
    runtime.append_block(
        FragmentBlock((("", "command query"),)),
        kind="user",
    )
    live_block = FragmentBlock((("class:ps.title", "Shell running\noutput"),))
    task = asyncio.create_task(runtime.view_process(
        ProcessViewerRequest(fragments=(("", " "),), max_height=1),
        live_block,
    ))
    await asyncio.sleep(0)

    assert runtime.screen.process_viewer.active
    assert runtime.document.active_block == live_block
    assert runtime.document.active_kind == "operation"
    assert runtime.screen._process_viewer_height() == 1
    assert runtime.screen._content_input_gap_height() == 2
    assert runtime.screen._interaction_height() == 0
    assert not runtime.screen.input_area.filter()

    runtime.resolve_process_viewer("detach")
    assert await task == "detach"
    assert runtime.screen.process_viewer.active
    assert not runtime.screen.input_area.filter()

    final_block = FragmentBlock((("class:ps.title", "Shell completed"),))
    runtime.commit_process_viewer(final_block)

    assert runtime.document.active_block is None
    assert runtime.document.blocks[-1].block == final_block
    assert runtime.screen.input_area.filter()


@pytest.mark.anyio
async def test_foreground_process_completion_commits_in_place() -> None:
    application = _ApplicationStub()
    initial = {
        "ok": True,
        "session_id": "exec_shell",
        "command": "git pull",
        "status": "running",
        "origin": "tui_shell",
        "output_lines": ["Updating files"],
    }
    completed = {
        **initial,
        "status": "exited",
        "exit_code": 0,
        "output_lines": ["Updating files", "Already up to date."],
    }
    mind = SimpleNamespace(
        frontend=SimpleNamespace(application=application),
        native_coding=SimpleNamespace(
            exec_session_output_snapshot=AsyncMock(
                side_effect=[initial, completed],
            ),
        ),
    )
    runtime = TuiRuntime()

    result = await watch_exec_session(
        runtime,
        mind,
        "exec_shell",
        announce_detach=True,
    )

    assert result == "exited"
    assert runtime.document.active_block is None
    assert not runtime.screen.process_viewer.active
    assert runtime.screen.input_area.filter()
    text = "".join(
        value
        for _style, value in runtime.document.blocks[-1].block.fragments
    )
    assert "git pull" in text
    assert "Already up to date." in text


@pytest.mark.anyio
@pytest.mark.parametrize("terminal_width", [40, 80])
@pytest.mark.parametrize(
    "output_lines",
    [[], [f"line {index}" for index in range(12)]],
)
async def test_process_completion_preserves_total_layout_height(
    output_lines,
    terminal_width,
) -> None:
    runtime = TuiRuntime()
    runtime.screen._output_size = lambda: (terminal_width, 24)
    snapshot = {
        "ok": True,
        "session_id": "exec_shell",
        "command": "git pull",
        "status": "running",
        "origin": "tui_shell",
        "output_lines": output_lines,
    }
    live_block = exec_session_live_block(
        snapshot,
        terminal_width=terminal_width,
    )
    final_block = exec_session_summary_block(
        {**snapshot, "status": "exited", "exit_code": 0},
        terminal_width=terminal_width,
    )
    task = asyncio.create_task(runtime.view_process(
        PROCESS_VIEWER_FOCUS_REQUEST,
        live_block,
    ))
    await asyncio.sleep(0)
    running_height = runtime.screen._visible_height()

    runtime.resolve_process_viewer("exited")
    assert await task == "exited"
    runtime.commit_process_viewer(final_block)

    assert runtime.screen._visible_height() == running_height
