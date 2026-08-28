# -*- coding: utf-8 -*-

import sys
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, PropertyMock, patch

import pytest
from prompt_toolkit.utils import get_cwidth

from mind_app.native_coding import NativeCoding
from mind_app.native_coding.exec.process_session import ProcessSessionManager
from mind_app.tui.core.models import FragmentBlock
from mind_app.tui.core.interrupt import InterruptDisposition
from mind_app.tui.core.runtime import TuiRuntime
from mind_app.tui.core.styles import TUI_APPLICATION_OVERRIDES
from mind_app.tui.features.shell import run_shell_escape
from mind_app.tui.features.processes import (
    PS_INTERRUPT_GRACE_SEC,
    PS_OUTPUT_LIMIT,
    _interrupt_exec_session,
    _watch_detached_exec_session,
    append_exec_history_snapshot,
    append_exec_stream_snapshot,
    exec_session_detached_block,
    exec_session_summary_block,
    exec_session_transcript_block,
    exec_session_user_shell_block,
    manage_exec_sessions,
    stop_all_exec_sessions,
    _wait_for_exec_session_update,
    watch_user_shell_session,
)


class _ApplicationStub(object):
    def __init__(self) -> None:
        self.viewport = SimpleNamespace(width=80, height=24)
        self.views = []

    def emit(self, view) -> None:
        self.views.append(view)


def _workspace_runtime(
    *,
    coding=None,
    user_shell=None,
    **coding_attributes,
) -> SimpleNamespace:
    if coding is None:
        coding = SimpleNamespace(**coding_attributes)
    if user_shell is None:
        user_shell = coding
    return SimpleNamespace(coding=coding, user_shell=user_shell)


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
    runtime = TuiRuntime()
    runtime.screen._output_size = lambda: (80, 24)
    native_coding = SimpleNamespace(
        running_exec_sessions=AsyncMock(return_value={
            "count": 0,
            "items": [],
        }),
    )
    mind = SimpleNamespace(
        workspace_runtime=_workspace_runtime(coding=native_coding),
    )

    handled = await manage_exec_sessions(runtime, mind)

    assert handled
    assert runtime.document.active_block is None
    assert runtime.document.blocks[-1].kind == "operation"
    fragments = runtime.document.blocks[-1].display_block.fragments
    assert "".join(text for _style, text in fragments) == (
        "/ps\n\n"
        "Background terminals\n\n"
        "  • No background terminals running."
    )
    assert fragments[0] == ("class:prompt.command.slash", "/ps")


@pytest.mark.anyio
async def test_streaming_ps_appends_process_summaries_without_menu() -> None:
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
    mind = SimpleNamespace(workspace_runtime=_workspace_runtime(
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
            max_output_chars=PS_OUTPUT_LIMIT,
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
    command_style = TUI_APPLICATION_OVERRIDES.get_attrs_for_style_str(
        "class:ps.stream.command"
    )
    assert command_style.color == "ansicyan"
    assert command_style.dim is False
    stream_style = TUI_APPLICATION_OVERRIDES.get_attrs_for_style_str(
        "class:ps.stream"
    )
    assert stream_style.color == ""
    assert stream_style.dim is True
    title_style = TUI_APPLICATION_OVERRIDES.get_attrs_for_style_str(
        "class:ps.title"
    )
    assert title_style.color == ""
    assert title_style.bold is True

    active = runtime.document.active_block
    assert active is not None
    runtime.commit_active_stream_prefix(active, raw_text="model stream")
    runtime.set_active_renderable(
        FragmentBlock((("class:assistant", "model continuation"),)),
        kind="assistant",
        stream_continuation=True,
    )

    continued = "".join(
        value
        for _style, value in runtime.document.fragments(width=80)
    )
    assert continued.endswith(
        "  … and 1 more running\n\nmodel continuation"
    )


@pytest.mark.anyio
async def test_shell_escape_starts_user_shell_watcher() -> None:
    application = _ApplicationStub()
    user_shell = SimpleNamespace(
        start_user_shell_session=AsyncMock(return_value={
            "ok": True,
            "session_id": "exec_shell",
            "origin": "tui_shell",
        }),
    )
    mind = SimpleNamespace(
        frontend=SimpleNamespace(application=application),
        workspace_runtime=_workspace_runtime(user_shell=user_shell),
        conversation=SimpleNamespace(snapshot=lambda: {
            "cid": "cid_owner",
            "sid": "sid_owner",
        }),
    )

    class RuntimeStub(object):
        def __init__(self) -> None:
            self.started = None
            self.task = None

        def start_background_task(self, awaitable, *, name) -> None:
            self.started = (name, awaitable)
            self.task = asyncio.create_task(awaitable)

    runtime = RuntimeStub()

    async def watch_ready(*args, **kwargs) -> bool:
        kwargs["ready_event"].set()
        return True

    with (
        patch(
            "mind_app.tui.features.shell.default_shell_executable",
            return_value="shell",
        ),
        patch(
            "mind_app.tui.features.shell.watch_user_shell_session",
            new=AsyncMock(side_effect=watch_ready),
        ) as watch,
    ):
        handled = await run_shell_escape(runtime, mind, "!resolved arg")

    assert handled
    user_shell.start_user_shell_session.assert_awaited_once_with(
        command="resolved arg",
        args=["shell", "-lc", "resolved arg"],
        timeout_sec=3600,
        owner_cid="cid_owner",
        owner_sid="sid_owner",
    )
    watch.assert_awaited_once()
    assert watch.call_args.args[:3] == (runtime, mind, "exec_shell")
    assert watch.call_args.kwargs["announce_detach"] is True
    assert runtime.started[0] == "shell exec cell exec_shell"
    await runtime.task


@pytest.mark.anyio
async def test_shell_escape_background_task_keeps_input_visible() -> None:
    snapshot = {
        "ok": True,
        "session_id": "exec_shell",
        "command": "adb devices",
        "status": "running",
        "origin": "tui_shell",
        "output_lines": ["List of devices attached"],
    }
    application = _ApplicationStub()
    user_shell = SimpleNamespace(
        start_user_shell_session=AsyncMock(return_value=snapshot),
        running_exec_sessions=AsyncMock(return_value={
            "count": 1,
            "items": [snapshot],
        }),
        exec_session_output_snapshot=AsyncMock(return_value=snapshot),
        wait_exec_session_update=AsyncMock(return_value={"changed": False}),
    )
    mind = SimpleNamespace(
        frontend=SimpleNamespace(application=application),
        workspace_runtime=_workspace_runtime(user_shell=user_shell),
    )
    runtime = TuiRuntime()

    with (
        patch(
            "mind_app.tui.features.shell.default_shell_executable",
            return_value="shell",
        ),
    ):
        handled = await run_shell_escape(runtime, mind, "!adb devices")

    assert handled
    assert runtime.inline_process_session_id == "exec_shell"
    assert runtime.document.active_gap_before == 1
    assert runtime.screen.bottom_pane.active_surface is None
    assert runtime.screen.input_area.filter()
    assert runtime.screen.input_footer.filter()
    assert runtime.screen._process_status_height() == 0
    assert runtime._background_tasks

    tasks = tuple(runtime._background_tasks)
    for task in tasks:
        task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)

    assert runtime.inline_process_session_id == ""


@pytest.mark.anyio
async def test_shell_escape_ctrl_c_interrupts_process_session() -> None:
    running = {
        "ok": True,
        "session_id": "exec_shell",
        "command": "ping -t 8.8.8.8",
        "status": "running",
        "origin": "tui_shell",
        "output_lines": ["reply"],
    }
    exited = {
        **running,
        "status": "exited",
        "exit_code": 130,
    }
    state = {"interrupted": False}

    async def control_exec_session(**_kwargs):
        state["interrupted"] = True
        return exited

    async def output_snapshot(**_kwargs):
        return exited if state["interrupted"] else running

    user_shell = SimpleNamespace(
        start_user_shell_session=AsyncMock(return_value=running),
        running_exec_sessions=AsyncMock(return_value={
            "count": 1,
            "items": [{
                "session_id": "exec_shell",
                "command": "ping -t 8.8.8.8",
            }],
        }),
        exec_session_output_snapshot=AsyncMock(side_effect=output_snapshot),
        control_exec_session=AsyncMock(side_effect=control_exec_session),
        wait_exec_session_update=AsyncMock(return_value={"changed": False}),
    )
    mind = SimpleNamespace(
        frontend=SimpleNamespace(application=_ApplicationStub()),
        workspace_runtime=_workspace_runtime(user_shell=user_shell),
    )
    runtime = TuiRuntime()

    with (
        patch(
            "mind_app.tui.features.shell.default_shell_executable",
            return_value="shell",
        ),
    ):
        assert await run_shell_escape(
            runtime,
            mind,
            "!ping -t 8.8.8.8",
        )

    tasks = tuple(runtime._background_tasks)
    runtime.input_model.handle_interrupt(runtime.screen.input.buffer)
    await asyncio.gather(*tasks)

    user_shell.control_exec_session.assert_awaited_once_with(
        session_id="exec_shell",
        control="interrupt",
    )
    assert runtime.document.active_block is None


@pytest.mark.anyio
async def test_interrupt_exec_session_returns_control_snapshot_immediately() -> None:
    exited = {
        "ok": True,
        "session_id": "exec_shell",
        "status": "exited",
        "exit_code": 130,
    }
    execution = SimpleNamespace(
        control_exec_session=AsyncMock(return_value=exited),
        exec_session_output_snapshot=AsyncMock(),
    )

    with patch(
        "mind_app.tui.features.processes.asyncio.sleep",
        new_callable=AsyncMock,
    ) as sleep:
        result = await _interrupt_exec_session(
            "exec_shell",
            execution=execution,
        )

    assert result == exited
    sleep.assert_not_awaited()
    execution.exec_session_output_snapshot.assert_not_awaited()


@pytest.mark.anyio
async def test_interrupt_exec_session_force_stops_unresponsive_process() -> None:
    running = {
        "ok": True,
        "session_id": "exec_shell",
        "status": "running",
    }
    exited = {
        **running,
        "status": "exited",
        "exit_code": 1,
    }
    execution = SimpleNamespace(
        control_exec_session=AsyncMock(side_effect=[running, exited]),
        exec_session_output_snapshot=AsyncMock(return_value=running),
    )

    with patch(
        "mind_app.tui.features.processes.asyncio.sleep",
        new_callable=AsyncMock,
    ) as sleep:
        result = await _interrupt_exec_session(
            "exec_shell",
            execution=execution,
        )

    assert result == exited
    sleep.assert_awaited_once_with(PS_INTERRUPT_GRACE_SEC)
    assert [
        call.kwargs["control"]
        for call in execution.control_exec_session.await_args_list
    ] == ["interrupt", "kill"]


@pytest.mark.anyio
async def test_second_shell_shows_first_shell_in_process_status() -> None:
    current = {
        "ok": True,
        "session_id": "exec_second",
        "command": "adb devices",
        "status": "running",
        "origin": "tui_shell",
        "output_lines": [],
    }
    async def wait_for_update(*_args, **_kwargs):
        await asyncio.sleep(0)
        return {"changed": False}

    user_shell = SimpleNamespace(
        start_user_shell_session=AsyncMock(return_value=current),
        running_exec_sessions=AsyncMock(return_value={
            "count": 2,
            "items": [
                {
                    "session_id": "exec_first",
                    "command": "ping -t 8.8.8.8",
                    "origin": "tui_shell",
                },
                {
                    "session_id": "exec_second",
                    "command": "adb devices",
                    "origin": "tui_shell",
                },
            ],
        }),
        exec_session_output_snapshot=AsyncMock(return_value=current),
        wait_exec_session_update=wait_for_update,
    )
    mind = SimpleNamespace(
        frontend=SimpleNamespace(application=_ApplicationStub()),
        workspace_runtime=_workspace_runtime(user_shell=user_shell),
    )
    runtime = TuiRuntime()

    with (
        patch(
            "mind_app.tui.features.shell.default_shell_executable",
            return_value="shell",
        ),
    ):
        assert await run_shell_escape(runtime, mind, "!adb devices")

    assert runtime.inline_process_session_id == "exec_second"
    assert runtime.screen.process_status.label == ""
    assert runtime.screen.user_shell_status.label == (
        "1 background terminal running · /ps to view · /stop to close"
    )
    assert runtime.screen.background_shell_status.label == (
        "1 background terminal running · /ps to view · /stop to close"
    )
    assert runtime.screen._process_status_height() == 1

    tasks = tuple(runtime._background_tasks)
    for task in tasks:
        task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)


@pytest.mark.anyio
async def test_user_shell_session_is_listed_and_closed(tmp_path) -> None:
    coding = NativeCoding(root=tmp_path)
    snapshot = await coding.user_shell.start_user_shell_session(
        command="background test",
        args=[
            sys.executable,
            "-c",
            "import time; print('ready', flush=True); time.sleep(30)",
        ],
        timeout_sec=60,
        idle_timeout_sec=60,
        owner_cid="cid_owner",
        owner_sid="sid_owner",
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
        assert item["background"] is False
        assert item["owner_cid"] == "cid_owner"
        assert item["owner_sid"] == "sid_owner"
        assert snapshot["owner_cid"] == "cid_owner"
        assert snapshot["owner_sid"] == "sid_owner"
        assert "ready" in snapshot["output"]

        assert await coding.mark_exec_session_background(session_id)
        running = await coding.running_exec_sessions()
        background_item = next(
            item for item in running["background_items"]
            if item["session_id"] == session_id
        )
        assert background_item["background"] is True
    finally:
        await coding.close()

    assert (await coding.running_exec_sessions())["count"] == 0


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("current_session", "expected_blocks"),
    (
        (("cid_owner", "sid_owner"), 1),
        (("cid_other", "sid_other"), 0),
    ),
)
async def test_detached_shell_completion_stays_with_owning_conversation(
    current_session,
    expected_blocks,
) -> None:
    snapshot = {
        "ok": True,
        "session_id": "exec_shell",
        "command": "long task",
        "status": "exited",
        "exit_code": 0,
        "origin": "tui_shell",
        "owner_cid": "cid_owner",
        "owner_sid": "sid_owner",
        "output_lines": ["complete"],
    }
    blocks = []
    completions = []
    runtime = SimpleNamespace(
        wait_for_process_routing_boundary=AsyncMock(),
        append_history_block=lambda block, **kwargs: (
            blocks.append((block, kwargs)) or True
        ),
        settle_detached_inline_process=lambda session_id: None,
        queue_background_block=lambda block, **kwargs: blocks.append(
            (block, kwargs)
        ),
        retain_process_completion=lambda snapshot, **kwargs: completions.append(
            (snapshot, kwargs)
        ),
    )
    mind = SimpleNamespace(
        frontend=SimpleNamespace(application=_ApplicationStub()),
        workspace_runtime=_workspace_runtime(
            exec_session_output_snapshot=AsyncMock(return_value=snapshot),
        ),
        conversation=SimpleNamespace(snapshot=lambda: {
            "cid": current_session[0],
            "sid": current_session[1],
        }),
    )

    await _watch_detached_exec_session(
        runtime,
        mind,
        "exec_shell",
        execution=mind.workspace_runtime.coding,
    )

    assert len(blocks) == expected_blocks
    assert len(completions) == 1 - expected_blocks
    if completions:
        assert completions[0][1]["label"] == "long task completed"


@pytest.mark.anyio
@pytest.mark.parametrize("conversation_changed", (False, True))
async def test_detached_shell_completion_waits_for_command_scope_result(
    conversation_changed,
) -> None:
    snapshot = {
        "ok": True,
        "session_id": "exec_shell",
        "command": "long task",
        "status": "exited",
        "exit_code": 0,
        "origin": "tui_shell",
        "owner_cid": "cid_owner",
        "owner_sid": "sid_owner",
        "output_lines": ["complete"],
    }
    current = {"cid": "cid_owner", "sid": "sid_owner"}
    runtime = TuiRuntime()
    runtime.begin_command_layout()
    mind = SimpleNamespace(
        frontend=SimpleNamespace(application=_ApplicationStub()),
        workspace_runtime=_workspace_runtime(
            exec_session_output_snapshot=AsyncMock(return_value=snapshot),
        ),
        conversation=SimpleNamespace(snapshot=lambda: dict(current)),
    )

    task = asyncio.create_task(
        _watch_detached_exec_session(
            runtime,
            mind,
            "exec_shell",
            execution=mind.workspace_runtime.coding,
        )
    )
    await asyncio.sleep(0)

    assert not task.done()
    assert not runtime.document.blocks
    assert runtime.process_completion_snapshots() == ()

    if conversation_changed:
        current.update(cid="cid_other", sid="sid_other")
    runtime.finish_command_layout()
    await task

    if conversation_changed:
        assert not runtime.document.blocks
        assert len(runtime.process_completion_snapshots()) == 1
        assert runtime.screen.process_status.label == "long task completed"
    else:
        assert len(runtime.document.blocks) == 1
        assert "You ran long task" in "".join(
            value for _style, value in runtime.document.blocks[0].display_block.fragments
        )
        assert runtime.process_completion_snapshots() == ()


@pytest.mark.anyio
async def test_exec_command_keeps_tool_policy_and_result_flow(tmp_path) -> None:
    coding = NativeCoding(root=tmp_path)
    try:
        result = await coding.exec_command(
            command="echo shared-session",
            yield_time_ms=2000,
            timeout_sec=30,
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
        "terminate_process_tree",
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
async def test_stop_all_stops_immediately_and_cancels_background_watchers() -> None:
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
    cancelled = []
    status_labels = []

    runtime = SimpleNamespace(
        cancel_background_session_task=cancelled.append,
        set_process_status_label=status_labels.append,
        set_user_shell_status_label=lambda _label: None,
        set_background_shell_status_label=lambda _label: None,
        process_completion_snapshots=lambda: (),
        inline_process_session_id="",
        inline_process_session_ids=(),
        background_process_session_ids=frozenset(),
    )
    mind = SimpleNamespace(
        frontend=SimpleNamespace(application=application),
        workspace_runtime=_workspace_runtime(coding=native_coding),
    )

    result = await stop_all_exec_sessions(runtime, mind)

    assert result is None
    assert cancelled == ["exec_shell", "exec_tool"]
    assert status_labels == [""]
    native_coding.running_exec_sessions.assert_awaited_once_with()
    native_coding.stop_exec_sessions.assert_awaited_once_with(
        session_ids=("exec_shell", "exec_tool"),
    )
    assert len(application.views) == 1
    text = "".join(
        value
        for _style, value in application.views[0].renderable.fragments
    )
    assert text == "• Stopping all background terminals."
    assert application.views[0].type == "tui.exec.stopping"


@pytest.mark.anyio
async def test_stop_all_without_background_terminals_keeps_stopping_message() -> None:
    application = _ApplicationStub()
    snapshot = {
        "count": 0,
        "items": [],
    }
    native_coding = SimpleNamespace(
        running_exec_sessions=AsyncMock(side_effect=[snapshot, snapshot]),
        stop_exec_sessions=AsyncMock(),
    )
    runtime = SimpleNamespace(
        process_completion_snapshots=lambda: (),
        inline_process_session_id="",
        inline_process_session_ids=(),
        background_process_session_ids=frozenset(),
    )
    mind = SimpleNamespace(
        frontend=SimpleNamespace(application=application),
        workspace_runtime=_workspace_runtime(coding=native_coding),
    )

    result = await stop_all_exec_sessions(runtime, mind)

    assert result is None
    native_coding.stop_exec_sessions.assert_not_awaited()
    assert len(application.views) == 1
    text = "".join(
        value
        for _style, value in application.views[0].renderable.fragments
    )
    assert text == "• Stopping all background terminals."


@pytest.mark.anyio
async def test_ps_snapshot_does_not_acknowledge_completed_history() -> None:
    runtime = TuiRuntime()
    runtime.retain_process_completion(
        {
            "ok": True,
            "session_id": "exec_complete",
            "command": "long task",
            "status": "exited",
            "exit_code": 0,
            "origin": "tui_shell",
            "output_lines": ["complete"],
        },
        label="long task completed",
    )
    mind = SimpleNamespace(
        workspace_runtime=_workspace_runtime(
            running_exec_sessions=AsyncMock(return_value={
                "count": 0,
                "items": [],
            }),
        ),
    )

    handled = await manage_exec_sessions(runtime, mind)

    assert handled
    assert runtime.process_completion_snapshots()
    assert runtime.screen.menu.active is False
    text = "".join(
        value
        for _style, value in runtime.document.blocks[-1].display_block.fragments
    )
    assert text == (
        "/ps\n\n"
        "Background terminals\n\n"
        "  • No background terminals running."
    )


@pytest.mark.anyio
async def test_ps_appends_snapshot_without_opening_viewer() -> None:
    runtime = TuiRuntime()
    runtime.screen._output_size = lambda: (80, 24)
    session = {
        "session_id": "exec_shell",
        "command": "long task",
        "pid": 101,
        "origin": "tui_shell",
    }
    output_snapshot = AsyncMock(return_value={
        **session,
        "ok": True,
        "status": "running",
        "output_lines": ["line 1", "line 2"],
    })
    mind = SimpleNamespace(
        workspace_runtime=_workspace_runtime(
            running_exec_sessions=AsyncMock(return_value={
                "count": 1,
                "items": [session],
            }),
            exec_session_output_snapshot=output_snapshot,
        ),
    )

    await manage_exec_sessions(runtime, mind)

    output_snapshot.assert_awaited_once_with(
        session_id="exec_shell",
        max_output_chars=PS_OUTPUT_LIMIT,
    )
    assert runtime.screen.menu.active is False
    text = "".join(
        value
        for _style, value in runtime.document.blocks[-1].display_block.fragments
    )
    assert text.startswith("/ps\n\nBackground terminals\n\n")
    assert "  • long task\n    ↳ line 1\n      line 2" in text


@pytest.mark.anyio
async def test_ps_excludes_inline_cell_but_keeps_detached_exec() -> None:
    blocks: list[FragmentBlock] = []
    runtime = SimpleNamespace(
        terminal_width=80,
        inline_process_session_id="exec_current",
        inline_process_session_ids=("exec_current",),
        background_process_session_ids=frozenset(),
        append_block=lambda block, *, kind: blocks.append(block),
    )
    current = {
        "session_id": "exec_current",
        "command": "foreground shell",
        "origin": "tui_shell",
    }
    detached = {
        "session_id": "exec_detached",
        "command": "background shell",
        "origin": "tui_shell",
    }
    output_snapshot = AsyncMock(side_effect=lambda **kwargs: {
        **detached,
        **kwargs,
        "ok": True,
        "status": "running",
        "output_lines": ["detached output"],
    })
    mind = SimpleNamespace(
        workspace_runtime=_workspace_runtime(
            running_exec_sessions=AsyncMock(return_value={
                "count": 2,
                "items": [current, detached],
            }),
            exec_session_output_snapshot=output_snapshot,
        ),
    )

    await manage_exec_sessions(runtime, mind)

    output_snapshot.assert_awaited_once_with(
        session_id="exec_detached",
        max_output_chars=PS_OUTPUT_LIMIT,
    )
    text = "".join(value for _style, value in blocks[-1].fragments)
    assert "foreground shell" not in text
    assert "background shell" in text



@pytest.mark.anyio
async def test_inline_shell_skips_only_fully_unchanged_render_blocks() -> None:
    application = _ApplicationStub()
    tail = [f"tail {index}" for index in range(8)]
    initial = {
        "ok": True,
        "session_id": "exec_shell",
        "command": "long task",
        "status": "running",
        "origin": "tui_shell",
        "output_lines": ["original", *tail],
    }
    unchanged = {**initial, "polled_at": 1}
    transcript_changed = {
        **initial,
        "output_lines": ["replacement", *tail],
    }
    exited = {
        **transcript_changed,
        "status": "exited",
        "exit_code": 0,
    }

    assert exec_session_user_shell_block(
        initial,
        terminal_width=80,
    ) != exec_session_user_shell_block(
        transcript_changed,
        terminal_width=80,
    )
    assert exec_session_transcript_block(initial) != (
        exec_session_transcript_block(transcript_changed)
    )
    assert exec_session_user_shell_block(
        transcript_changed,
        terminal_width=80,
    ) != exec_session_user_shell_block(
        exited,
        terminal_width=80,
    )

    user_shell = SimpleNamespace(
        running_exec_sessions=AsyncMock(return_value={
            "count": 1,
            "items": [initial],
        }),
        exec_session_output_snapshot=AsyncMock(
            side_effect=[unchanged, transcript_changed, exited],
        ),
        wait_exec_session_update=AsyncMock(side_effect=[
            {
                "changed": True,
                "event": "delta",
                "delta": [{"stream": "stdout", "text": "replacement"}],
                "snapshot": unchanged,
            },
            {
                "changed": True,
                "event": "delta",
                "delta": [{"stream": "stdout", "text": "replacement"}],
                "snapshot": transcript_changed,
            },
            {
                "changed": True,
                "event": "completed",
                "delta": [],
                "snapshot": exited,
            },
        ]),
    )
    mind = SimpleNamespace(
        frontend=SimpleNamespace(application=application),
        workspace_runtime=_workspace_runtime(user_shell=user_shell),
    )
    runtime = TuiRuntime()

    events: list[str] = []

    async def handoff() -> None:
        events.append("handoff")

    original_begin = runtime.begin_inline_process

    def begin(*args, **kwargs):
        events.append("begin")
        return original_begin(*args, **kwargs)

    with (
        patch.object(runtime, "handoff_inline_process", side_effect=handoff) as handoff_mock,
        patch.object(runtime, "begin_inline_process", side_effect=begin),
        patch.object(
            runtime,
            "update_inline_process",
            wraps=runtime.update_inline_process,
        ) as update,
    ):
        result = await watch_user_shell_session(
            runtime,
            mind,
            "exec_shell",
            initial_snapshot=initial,
        )

    assert result == "exited"
    assert events[:2] == ["handoff", "begin"]
    handoff_mock.assert_awaited_once()
    assert update.call_count >= 2
    update.assert_any_call(
        exec_session_user_shell_block(
            transcript_changed,
            terminal_width=80,
        ),
        session_id="exec_shell",
        transcript_block=exec_session_transcript_block(transcript_changed),
        gap_before=1,
    )
    assert runtime.document.active_block is None
    assert "replacement" in "".join(
        text
        for _style, text in runtime.document.blocks[-1].transcript_block.fragments
    )


@pytest.mark.anyio
async def test_inline_process_returns_detach_without_hiding_input() -> None:
    runtime = TuiRuntime()
    future = runtime.begin_inline_process(
        "exec_shell",
        FragmentBlock((("class:ps.title", "Shell running"),)),
    )

    runtime.resolve_inline_process("detach", session_id="exec_shell")

    assert await future == "detach"
    assert runtime.screen.bottom_pane.active_surface is None
    assert runtime.screen.input_area.filter()

    runtime.dismiss_inline_process("exec_shell")

    assert runtime.inline_process_session_id == ""


def test_background_completion_waits_for_stream_boundary() -> None:
    runtime = TuiRuntime()
    block = FragmentBlock((("class:ps.title", "Shell completed"),))
    runtime.set_execution_active(True)

    runtime.queue_background_block(block)

    assert not runtime.document.blocks
    assert len(runtime._background_blocks) == 1
    deferred = runtime._background_blocks[0]
    assert deferred.block == block
    assert deferred.transcript_block == block
    assert deferred.activity_lease is None

    runtime.set_execution_active(False)

    assert runtime.document.blocks[-1].display_block == block
    assert not runtime._background_blocks


def test_shell_live_cell_writes_tree_summary_into_document() -> None:
    block = exec_session_user_shell_block(
        {
            "ok": True,
            "session_id": "exec_shell",
            "command": "adb devices",
            "status": "running",
            "origin": "tui_shell",
            "output_lines": ["List of devices attached", "device-1"],
        },
        terminal_width=80,
    )

    text = "".join(value for _style, value in block.fragments)

    assert text == (
        "• Running adb devices\n"
        "  └ List of devices attached\n"
        "    device-1"
    )


def test_user_shell_exec_cell_keeps_head_tail_with_ellipsis() -> None:
    block = exec_session_user_shell_block(
        {
            "command": "adb logcat",
            "status": "running",
            "output_lines": [f"line {index}" for index in range(70)],
        },
        terminal_width=80,
    )
    text = "".join(value for _style, value in block.fragments)

    assert text.startswith("• Running adb logcat\n  └ line 0")
    assert "line 23" in text
    assert "… +21 lines (ctrl + t to view transcript)" in text
    assert "line 45" in text
    assert text.endswith("line 69")
    assert "line 24" not in text


def test_user_shell_exec_cell_counts_omitted_logical_lines_after_wrapping() -> None:
    block = exec_session_user_shell_block(
        {
            "command": "long output",
            "status": "running",
            "output_lines": [f"line {index} " + ("x" * 200) for index in range(16)],
        },
        terminal_width=60,
    )
    text = "".join(value for _style, value in block.fragments)

    assert "… +4 lines (ctrl + t to view transcript)" in text
    assert "line 0" in text
    assert "line 15" in text
    assert "line 6" not in text
    assert "line 9" not in text


def test_user_shell_exec_cell_uses_animated_activity_marker() -> None:
    with patch(
        "mind_app.tui.features.processes.time.perf_counter",
        return_value=0.0,
    ):
        block = exec_session_user_shell_block(
            {
                "command": "long task",
                "status": "running",
                "output_lines": [],
            },
            terminal_width=80,
            animated=True,
        )

    marker_style, marker = block.fragments[0]
    assert marker_style.startswith("fg:#")
    assert marker in {"•", "◦"}


def test_user_shell_failure_title_omits_exit_code_suffix() -> None:
    block = exec_session_user_shell_block(
        {
            "command": "ping -t 8.8.8.8",
            "status": "exited",
            "exit_code": 1,
            "origin": "tui_shell",
            "output_lines": ["reply"],
        },
        terminal_width=80,
        running=False,
    )
    text = "".join(value for _style, value in block.fragments)

    assert text.splitlines()[0] == "• You ran ping -t 8.8.8.8"
    assert "exit 1" not in text


def test_detached_user_shell_title_omits_session_id_suffix() -> None:
    output_lines = [f"line {index}" for index in range(70)]
    block = exec_session_detached_block(
        {
            "command": "ping -t 8.8.8.8",
            "session_id": "exec_2b920fa3b4400938",
            "status": "running",
            "origin": "tui_shell",
            "output_lines": output_lines,
        },
        terminal_width=80,
    )
    text = "".join(value for _style, value in block.fragments)

    assert text.splitlines()[0] == "• Running ping -t 8.8.8.8"
    assert "exec_2b920fa3b4400938" not in text
    assert "line 0" in text
    assert "line 69" in text
    assert "line 24" not in text
    assert "… +21 lines (ctrl + t to view transcript)" in text


def test_process_transcript_keeps_full_command_and_retained_output() -> None:
    command = "Get-ChildItem\n| Select-Object Name"
    output = "\n".join(f"line {index}" for index in range(40))

    block = exec_session_transcript_block({
        "command": command,
        "output": output,
        "output_lines": ["tail only"],
    })
    text = "".join(value for _style, value in block.fragments)

    assert text.startswith(f"$ {command}\n")
    assert "line 0" in text
    assert "line 39" in text
    assert "tail only" not in text


def test_foreground_completion_commits_before_barrier_release() -> None:
    runtime = TuiRuntime()
    block = FragmentBlock((("class:ps.title", "Operation ready"),))
    runtime.set_foreground_active(True)

    runtime.queue_background_block(block)

    assert runtime.document.blocks[-1].display_block == block
    assert not runtime._background_blocks


@pytest.mark.anyio
async def test_inline_process_uses_transcript_without_replacing_input() -> None:
    runtime = TuiRuntime()
    runtime.append_block(
        FragmentBlock((("", "command query"),)),
        kind="user",
    )
    live_block = FragmentBlock((("class:ps.title", "Shell running\noutput"),))
    future = runtime.begin_inline_process(
        "exec_shell",
        live_block,
    )

    assert runtime.document.active_block == live_block
    assert runtime.document.active_kind == "operation"
    active_view = runtime.screen._active_view_layout()
    assert active_view.surface is None
    assert active_view.total_height == 0
    assert runtime.screen._bottom_pane_top_inset_height() == 1
    assert not runtime.screen.active_view_area.filter()
    assert runtime.screen.input_area.filter()

    runtime.resolve_inline_process("done", session_id="exec_shell")
    assert await future == "done"

    final_block = FragmentBlock((("class:ps.title", "Shell completed"),))
    runtime.commit_inline_process(final_block, session_id="exec_shell")

    assert runtime.document.active_block is None
    assert runtime.document.blocks[-1].display_block == final_block
    assert runtime.screen.input_area.filter()


@pytest.mark.anyio
async def test_inline_process_keeps_input_and_footer_visible() -> None:
    runtime = TuiRuntime()
    live_block = FragmentBlock((("class:ps.title", "Shell running\noutput"),))
    future = runtime.begin_inline_process(
        "exec_shell",
        live_block,
    )

    assert runtime.inline_process_session_id == "exec_shell"
    assert runtime.screen.bottom_pane.active_surface is None
    assert not runtime.screen.active_view_area.filter()
    assert runtime.screen.input_area.filter()
    assert runtime.screen.input_footer.filter()
    assert runtime.screen._active_view_layout().total_height == 0
    assert runtime.screen._process_status_height() == 0

    runtime.resolve_inline_process("detach", session_id="exec_shell")
    assert await future == "detach"

    runtime.dismiss_inline_process("exec_shell")
    assert runtime.inline_process_session_id == ""


@pytest.mark.anyio
async def test_inline_process_updates_and_commits_one_dynamic_cell() -> None:
    runtime = TuiRuntime()
    live_block = FragmentBlock((("", "Shell running"),))
    updated_block = FragmentBlock((("", "Shell running\noutput"),))
    final_block = FragmentBlock((("", "Shell completed"),))

    future = runtime.begin_inline_process("exec_shell", live_block)
    runtime.update_inline_process(updated_block, session_id="exec_shell")
    assert runtime.document.active_block == updated_block

    runtime.resolve_inline_process("exited", session_id="exec_shell")
    assert await future == "exited"
    runtime.commit_inline_process(final_block, session_id="exec_shell")

    assert runtime.document.active_block is None
    assert runtime.document.blocks[-1].display_block == final_block


@pytest.mark.anyio
async def test_ctrl_c_clears_draft_before_interrupting_inline_shell() -> None:
    runtime = TuiRuntime()
    task = runtime.begin_inline_process(
        "exec_shell",
        FragmentBlock((("", "• Shell ping -t 8.8.8.8"),)),
    )

    runtime.screen.input.buffer.text = "draft"
    disposition = runtime.input_model.handle_interrupt(
        runtime.screen.input.buffer
    )

    assert disposition is InterruptDisposition.DRAFT_DISCARDED
    assert not task.done()
    assert runtime.screen.input.buffer.text == ""
    assert not runtime.submissions.interrupt_state.exit_armed
    assert "again to exit" not in "".join(
        text for _style, text in runtime.screen._footer_fragments()
    )
    assert runtime.inline_process_session_id == "exec_shell"

    disposition = runtime.input_model.handle_interrupt(
        runtime.screen.input.buffer
    )

    assert disposition is InterruptDisposition.CONSUMED
    assert await task == "interrupt"
    assert runtime.submissions.interrupt_state.exit_armed

    disposition = runtime.input_model.handle_interrupt(
        runtime.screen.input.buffer
    )

    assert disposition is InterruptDisposition.EXIT_REQUESTED
    assert runtime.submissions.interrupt_state.exit_requested

    runtime.dismiss_inline_process("exec_shell")
    assert runtime.inline_process_session_id == ""


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
    user_shell = SimpleNamespace(
        running_exec_sessions=AsyncMock(return_value={
            "count": 1,
            "items": [initial],
        }),
        exec_session_output_snapshot=AsyncMock(
            side_effect=[initial, completed],
        ),
        wait_exec_session_update=AsyncMock(return_value={
            "changed": True,
            "event": "completed",
            "delta": [],
            "snapshot": completed,
        }),
    )
    mind = SimpleNamespace(
        frontend=SimpleNamespace(application=application),
        workspace_runtime=_workspace_runtime(user_shell=user_shell),
    )
    runtime = TuiRuntime()

    result = await watch_user_shell_session(
        runtime,
        mind,
        "exec_shell",
        announce_detach=True,
        initial_snapshot=initial,
    )

    assert result == "exited"
    assert runtime.document.active_block is None
    assert runtime.screen.input_area.filter()
    assert runtime.screen.input_footer.filter()
    text = "".join(
        value
        for _style, value in runtime.document.blocks[-1].display_block.fragments
    )
    assert text == (
        "• You ran git pull\n"
        "  └ Updating files\n"
        "    Already up to date."
    )


@pytest.mark.anyio
async def test_detached_shell_completion_appends_after_original_stable_cell() -> None:
    runtime = TuiRuntime()
    running = FragmentBlock((
        ("class:shell.title.action", "• Shell ping"),
        ("", "\n"),
        ("class:ps.output", "  └ reply 1\n    reply 2"),
    ))
    detached = FragmentBlock((
        ("class:ps.title", "• Shell ping"),
        ("", "\n"),
        ("class:ps.output", "  └ reply 2"),
    ))
    completed = FragmentBlock((
        ("class:shell.title.action", "• You ran ping"),
        ("", "\n"),
        ("class:ps.output", "  └ reply 2\n    statistics"),
    ))

    runtime.begin_inline_process("exec_shell", running)
    runtime.resolve_inline_process("detach", session_id="exec_shell")
    runtime.commit_inline_process(
        detached,
        session_id="exec_shell",
        retain_for_background=True,
    )
    runtime.mark_inline_process_background("exec_shell")

    assert len(runtime.document.blocks) == 1
    runtime.document.replace_blocks(runtime.document.blocks)
    runtime.append_history_block(completed, kind="operation")
    runtime.settle_detached_inline_process("exec_shell")
    assert len(runtime.document.blocks) == 2
    assert runtime.document.blocks[0].display_block == detached
    assert runtime.document.blocks[1].display_block == completed
    assert not runtime.background_process_session_ids


@pytest.mark.anyio
async def test_multiple_inline_shells_update_their_own_stable_cells() -> None:
    runtime = TuiRuntime()
    first = FragmentBlock((("", "• Running first"),))
    second = FragmentBlock((("", "• Running second"),))
    first_update = FragmentBlock((("", "• You ran first"),))
    second_update = FragmentBlock((("", "• You ran second"),))

    runtime.begin_inline_process("exec_first", first)
    runtime.begin_inline_process("exec_second", second)

    runtime.update_inline_process(
        first_update,
        session_id="exec_first",
    )
    assert runtime.document.blocks[0].display_block == first_update

    runtime.resolve_inline_process("done", session_id="exec_first")
    runtime.commit_inline_process(
        first_update,
        session_id="exec_first",
    )
    runtime.resolve_inline_process("done", session_id="exec_second")
    runtime.commit_inline_process(
        second_update,
        session_id="exec_second",
    )

    assert [
        item.display_block
        for item in runtime.document.blocks
    ] == [first_update, second_update]
    assert runtime.inline_process_session_ids == ()


@pytest.mark.anyio
async def test_inline_process_handoff_commits_before_next_cell() -> None:
    runtime = TuiRuntime()
    first = FragmentBlock((("", "• Running first"),))
    second = FragmentBlock((("", "• Running second"),))

    runtime.begin_inline_process("exec_first", first)
    settle = AsyncMock()
    with (
        patch.object(TuiRuntime, "active", new_callable=PropertyMock, return_value=True),
        patch.object(runtime.viewport, "settle_scrollback", new=settle),
    ):
        await runtime.handoff_inline_process()
    settle.assert_awaited_once()

    assert runtime.document.active_block is None
    assert len(runtime.document.blocks) == 1
    assert runtime.document.blocks[0].display_block == first

    runtime.begin_inline_process("exec_second", second)
    assert runtime.document.active_block == second
    assert len(runtime.document.blocks) == 1

    runtime.resolve_inline_process("done", session_id="exec_first")
    runtime.commit_inline_process(first, session_id="exec_first")
    runtime.resolve_inline_process("done", session_id="exec_second")
    runtime.commit_inline_process(second, session_id="exec_second")


@pytest.mark.anyio
async def test_start_inline_process_resets_view_before_handoff() -> None:
    runtime = TuiRuntime()
    first = FragmentBlock((("", "• Running first"),))
    second = FragmentBlock((("", "• Running second"),))
    runtime.begin_inline_process("exec_first", first)
    runtime.viewport.view_row = 4
    observed_rows: list[int | None] = []

    async def settle() -> None:
        observed_rows.append(runtime.viewport.view_row)

    with (
        patch.object(TuiRuntime, "active", new_callable=PropertyMock, return_value=True),
        patch.object(runtime.viewport, "settle_scrollback", new=settle),
    ):
        await runtime.start_inline_process("exec_second", second)

    assert observed_rows == [None]
    assert runtime.document.active_block == second

    for session_id, block in (
        ("exec_first", first),
        ("exec_second", second),
    ):
        runtime.resolve_inline_process("done", session_id=session_id)
        runtime.commit_inline_process(block, session_id=session_id)


@pytest.mark.anyio
async def test_inline_process_starts_are_serialized() -> None:
    runtime = TuiRuntime()
    first = FragmentBlock((("", "• Running first"),))
    second = FragmentBlock((("", "• Running second"),))
    third = FragmentBlock((("", "• Running third"),))

    runtime.begin_inline_process("exec_first", first)
    settle_started = asyncio.Event()
    release_settle = asyncio.Event()

    async def settle() -> None:
        settle_started.set()
        await release_settle.wait()

    with (
        patch.object(TuiRuntime, "active", new_callable=PropertyMock, return_value=True),
        patch.object(runtime.viewport, "settle_scrollback", new=settle),
    ):
        second_task = asyncio.create_task(
            runtime.start_inline_process("exec_second", second),
        )
        await settle_started.wait()

        third_task = asyncio.create_task(
            runtime.start_inline_process("exec_third", third),
        )
        await asyncio.sleep(0)
        assert not third_task.done()

        release_settle.set()
        await second_task
        await third_task

    assert [
        item.display_block
        for item in runtime.document.blocks
    ] == [first, second]
    assert runtime.document.active_block == third

    for session_id, block in (
        ("exec_first", first),
        ("exec_second", second),
        ("exec_third", third),
    ):
        runtime.resolve_inline_process("done", session_id=session_id)
        runtime.commit_inline_process(block, session_id=session_id)


@pytest.mark.anyio
async def test_exec_session_update_timeout_does_not_request_snapshot() -> None:
    wait_update = AsyncMock(return_value=False)
    mind = SimpleNamespace(
        workspace_runtime=_workspace_runtime(
            wait_exec_session_update=wait_update,
        ),
    )

    update_event = await _wait_for_exec_session_update(
        "exec_shell",
        {"revision": 7},
        execution=mind.workspace_runtime.coding,
    )

    assert update_event is None
    wait_update.assert_awaited_once_with(
        session_id="exec_shell",
        revision=7,
        timeout_sec=1.0,
    )


@pytest.mark.anyio
async def test_native_exec_update_carries_completion_event_snapshot() -> None:
    snapshot = {
        "ok": True,
        "status": "exited",
        "exit_code": 0,
    }
    sessions = SimpleNamespace(
        wait_for_update=AsyncMock(return_value=True),
        output_delta=AsyncMock(return_value={
            "revision": 4,
            "reset": False,
            "items": [{
                "revision": 4,
                "stream": "stdout",
                "text": "done",
            }],
            "snapshot": snapshot,
        }),
        output_snapshot=AsyncMock(return_value=snapshot),
    )
    coding = NativeCoding.__new__(NativeCoding)
    coding._process_sessions = sessions

    update_event = await coding.wait_exec_session_update(
        "exec_shell",
        revision=3,
        timeout_sec=1.0,
    )

    assert update_event == {
        "changed": True,
        "event": "completed",
        "delta": [{
            "revision": 4,
            "stream": "stdout",
            "text": "done",
        }],
        "delta_reset": False,
        "snapshot": snapshot,
    }
    sessions.output_delta.assert_awaited_once_with(
        "exec_shell",
        revision=3,
    )
    sessions.output_snapshot.assert_awaited_once_with(
        "exec_shell",
        max_output_chars=120000,
    )


def test_user_shell_output_limit_counts_wrapped_display_rows() -> None:
    block = exec_session_user_shell_block(
        {
            "command": "echo long",
            "status": "running",
            "output_lines": ["abcdefghijklmno", "tail"],
        },
        terminal_width=12,
    )
    text = "".join(value for _style, value in block.fragments)

    assert "abcdefgh" in text
    assert "ijklmno" in text
    assert "tail" in text
    assert all(
        get_cwidth(line) <= 12
        for line in text.splitlines()
    )


def test_user_shell_output_limit_reports_dropped_history_lines() -> None:
    block = exec_session_user_shell_block(
        {
            "command": "ping",
            "status": "running",
            "output_lines": ["tail 1", "tail 2"],
            "output_lines_dropped": 80,
        },
        terminal_width=80,
    )
    text = "".join(value for _style, value in block.fragments)

    assert "… +80 lines" in text
    assert "tail 1" in text
    assert "tail 2" in text


@pytest.mark.anyio
@pytest.mark.parametrize("starts_exited", (False, True))
async def test_ps_cross_conversation_completion_does_not_commit_transcript(
    starts_exited,
) -> None:
    running = {
        "ok": True,
        "session_id": "exec_shell",
        "command": "long task",
        "status": "running",
        "origin": "tui_shell",
        "owner_cid": "cid_owner",
        "owner_sid": "sid_owner",
        "output_lines": ["working"],
    }
    completed = {
        **running,
        "status": "exited",
        "exit_code": 0,
        "output_lines": ["working", "complete"],
    }
    mind = SimpleNamespace(
        frontend=SimpleNamespace(application=_ApplicationStub()),
        workspace_runtime=_workspace_runtime(user_shell=SimpleNamespace(
            running_exec_sessions=AsyncMock(return_value={
                "count": 1,
                "items": [running],
            }),
            exec_session_output_snapshot=AsyncMock(return_value=completed),
            wait_exec_session_update=AsyncMock(return_value={
                "changed": True,
                "event": "completed",
                "delta": [],
                "snapshot": completed,
            }),
        )),
        conversation=SimpleNamespace(snapshot=lambda: {
            "cid": "cid_other",
            "sid": "sid_other",
        }),
    )
    runtime = TuiRuntime()

    result = await watch_user_shell_session(
        runtime,
        mind,
        "exec_shell",
        initial_snapshot=completed if starts_exited else running,
    )

    assert result == "exited"
    assert not runtime.document.blocks
    assert runtime.document.active_block is None
    assert len(runtime.process_completion_snapshots()) == 1
    assert runtime.screen.process_status.label == "long task completed"


@pytest.mark.anyio
@pytest.mark.parametrize("terminal_width", [40, 80])
@pytest.mark.parametrize(
    "output_lines",
    [[], [f"line {index}" for index in range(12)]],
)
async def test_inline_shell_completion_preserves_total_layout_height(
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
    live_block = exec_session_user_shell_block(
        snapshot,
        terminal_width=terminal_width,
    )
    final_block = exec_session_user_shell_block(
        {**snapshot, "status": "exited", "exit_code": 0},
        terminal_width=terminal_width,
        running=False,
    )
    future = runtime.begin_inline_process(
        "exec_shell",
        live_block,
    )
    running_height = runtime.screen._visible_height()

    runtime.resolve_inline_process("exited", session_id="exec_shell")
    assert await future == "exited"
    runtime.commit_inline_process(final_block, session_id="exec_shell")

    assert runtime.screen._visible_height() == running_height
