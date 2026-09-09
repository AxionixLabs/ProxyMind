# -*- coding: utf-8 -*-

import asyncio
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest
from agent.harness.process_lifecycle import ProcessLifecycle
from agent.ports.process_tools import ProcessSessionSnapshot
from prompt_toolkit.utils import get_cwidth

from frontends.tui.core.process_status import TuiProcessStatus
from frontends.tui.core.models import FragmentBlock
from frontends.tui.core.render import fragments_text
from frontends.tui.core.runtime import TuiRuntime
from frontends.tui.features.context import (
    exec_status_display_label,
)
from frontends.tui.features.processes import monitor_exec_status
from frontends.tui.features.summary import (
    CommandSummary,
    command_summary_text,
    command_summary_title_parts,
)


def test_process_status_is_a_dedicated_optional_row() -> None:
    runtime = TuiRuntime()

    assert runtime.screen._process_status_height() == 0

    runtime.set_process_status_label("pytest -q · +2")

    assert runtime.screen._process_status_height() == 1
    rendered = fragments_text(runtime.screen.process_status.fragments())
    assert rendered[0] in {"◦", "•"}
    assert rendered[1:] == " pytest -q · +2"
    assert "pytest -q" not in fragments_text(
        runtime.screen._footer_fragments()
    )
    bottom_children = runtime.screen.bottom_pane_area.content.children
    assert bottom_children.index(runtime.screen.status_window) < (
        bottom_children.index(runtime.screen.process_status_window)
    ) < bottom_children.index(runtime.screen.queued_window)
    command_fragment = next(
        fragment
        for fragment in runtime.screen.process_status.fragments()
        if fragment[1] == "pytest -q · +2"
    )
    assert command_fragment[0] != "class:process-status.background"

    runtime.set_process_status_label("")

    assert runtime.screen._process_status_height() == 0


def test_process_status_is_inline_when_activity_is_visible() -> None:
    runtime = TuiRuntime()
    runtime.screen.set_activity_renderable(
        FragmentBlock((("class:status", "• Thinking"),)),
    )
    runtime.set_process_status_label("ping -t 8.8.8.8")

    assert runtime.screen._process_status_height() == 0
    rendered = fragments_text(runtime.screen._status_fragments())
    assert rendered == (
        "• Thinking · ping -t 8.8.8.8"
    )

    runtime.set_process_status_label(
        "1 background terminal running · /ps to view · /stop to close"
    )
    inline = runtime.screen.process_status.inline_fragments()
    assert all(
        style == "class:process-status.background"
        for style, text in inline
        if text.strip()
    )


def test_background_shell_slot_switches_between_manual_and_all_sessions() -> None:
    runtime = TuiRuntime()
    runtime.screen.set_activity_renderable(
        FragmentBlock((("class:status", "• Thinking"),)),
    )
    runtime.set_user_shell_status_label(
        "1 background terminal running · /ps to view · /stop to close"
    )
    runtime.set_background_shell_status_label(
        "2 background terminals running · /ps to view · /stop to close"
    )

    assert runtime.screen._status_fragments() == [
        ("class:status", "• Thinking"),
    ]
    assert fragments_text(runtime.screen._process_status_fragments()) == (
        "• 1 background terminal running · /ps to view · /stop to close"
    )
    assert runtime.screen._process_status_height() == 1

    runtime.screen.clear_activity_renderable()

    assert fragments_text(runtime.screen._process_status_fragments()) == (
        "• 2 background terminals running · /ps to view · /stop to close"
    )


def test_exec_status_display_label_uses_actual_terminal_count() -> None:
    assert exec_status_display_label({
        "items": [{"origin": "tui_shell"}],
    }) == "1 background terminal running · /ps to view · /stop to close"
    assert exec_status_display_label({
        "items": [
            {"origin": "tui_shell"},
            {"origin": "tui_shell"},
        ],
    }) == "2 background terminals running · /ps to view · /stop to close"


def test_process_status_footer_is_static_dim() -> None:
    status = TuiRuntime().screen.process_status
    status.set_label("1 background terminal running · /ps to view · /stop to close")

    fragments = status.fragments()

    suffix = [
        fragment
        for fragment in fragments
        if fragment[0] == "class:process-status.background"
    ]
    assert suffix
    assert all(style == "class:process-status.background" for style, _ in suffix)
    assert "1 background terminal running" in fragments_text(suffix)
    action = [
        fragment
        for fragment in fragments
        if fragment[0] == "class:process-status.action"
    ]
    assert [text for _style, text in action] == ["/ps", "/stop"]
    assert " to view" in fragments_text(suffix)
    assert " to close" in fragments_text(suffix)


def test_process_status_summary_respects_terminal_display_width() -> None:
    label = exec_status_display_label(
        {
            "count": 2,
            "items": [{"command": "测试测试测试测试测试 command"}],
        },
        line_width=20,
    )

    status = TuiRuntime().screen.process_status
    status._get_width = lambda: 20
    status.set_label(label)
    rendered = fragments_text(status.fragments())

    assert get_cwidth(rendered) <= 20
    assert "exec" not in rendered


def test_process_status_remains_static() -> None:
    status = TuiProcessStatus(
        invalidate=lambda: None,
        get_width=lambda: 80,
    )

    status.set_label("adb logcat · +2")
    first = status.fragments()
    second = status.fragments()

    assert fragments_text(first) == "• adb logcat · +2"
    assert second == first


@pytest.mark.anyio
async def test_process_status_breathes_without_activity_slot() -> None:
    invalidate = Mock()

    runtime = TuiRuntime()
    status = runtime.screen.process_status
    status._invalidate = invalidate

    runtime.set_process_status_label("ping -t 8.8.8.8")
    first_refresh_count = invalidate.call_count
    assert runtime.activity.active is False

    await asyncio.sleep(0)
    assert invalidate.call_count > first_refresh_count
    assert runtime.activity.active is False

    runtime.set_process_status_label("")
    stopped_refresh_count = invalidate.call_count
    await asyncio.sleep(0)
    assert invalidate.call_count == stopped_refresh_count


def test_process_status_filters_controls_before_clipping() -> None:
    status = TuiProcessStatus(
        invalidate=lambda: None,
        get_width=lambda: 30,
    )

    status.set_label("id\tdevice\x1b]52;c;payload\x1b\\")
    rendered = fragments_text(status.fragments())

    assert "\x1b" not in rendered
    assert "payload" not in rendered
    assert get_cwidth(rendered) <= 30


def test_process_completion_status_precedes_running_status_until_acknowledged(
) -> None:
    runtime = TuiRuntime()
    runtime.set_process_status_label("other task")
    runtime.retain_process_completion(
        {
            "session_id": "exec_complete",
            "command": "long task",
            "status": "exited",
            "exit_code": 0,
        },
        label="long task completed",
    )

    runtime.set_process_status_label("new running task")

    assert runtime.screen.process_status.label == "long task completed"
    snapshots = runtime.process_completion_snapshots()
    assert snapshots[0]["session_id"] == "exec_complete"

    runtime.acknowledge_process_completion("exec_complete")

    assert runtime.screen.process_status.label == "new running task"
    assert runtime.process_completion_snapshots() == ()


def test_command_summary_bolds_action_but_not_command() -> None:
    parts = command_summary_title_parts(CommandSummary(
        kind="Started",
        command="adb logcat",
    ))

    assert any("bold" in style and text == "Started" for text, style in parts)
    assert any("bold" not in style and text == "adb logcat" for text, style in parts)


def test_command_summary_uses_actual_tree_prefix_width() -> None:
    block = command_summary_text(
        CommandSummary(
            kind="Shell",
            command="echo ready",
            lines=("first " * 20, "second " * 20),
        ),
        terminal_width=20,
        first_line_prefix="  └ ",
        line_prefix="    ",
    )
    lines = fragments_text(block.fragments).splitlines()

    assert all(get_cwidth(line) <= 20 for line in lines)
    assert lines[1].startswith("  └ ")
    assert lines[2].startswith("    ")


@pytest.mark.anyio
async def test_process_status_monitor_updates_and_clears_runtime() -> None:
    runtime = TuiRuntime()
    host = SimpleNamespace(
        lifecycle=ProcessLifecycle(),
        workspace_runtime=SimpleNamespace(
            coding=SimpleNamespace(
                exec_session_snapshots=AsyncMock(return_value=()),
                running_exec_sessions=AsyncMock(return_value={
                    "count": 1,
                    "items": [{"command": "pytest -q"}],
                }),
                wait_exec_sessions_update=AsyncMock(
                    side_effect=asyncio.CancelledError(),
                ),
            ),
        ),
    )

    with (
        patch.object(
            runtime,
            "set_process_status_label",
            wraps=runtime.set_process_status_label,
        ) as set_label,
    ):
        with pytest.raises(asyncio.CancelledError):
            await monitor_exec_status(runtime, host)

    coding = host.workspace_runtime.coding
    coding.running_exec_sessions.assert_awaited_once()
    coding.wait_exec_sessions_update.assert_awaited_once_with(
        revision=-1,
        timeout_sec=3600.0,
    )
    assert [call.args[0] for call in set_label.call_args_list] == [
        "1 background terminal running · /ps to view · /stop to close",
        "",
    ]
    assert runtime.screen.process_status.label == ""


@pytest.mark.anyio
async def test_process_status_monitor_splits_model_and_user_shell_sources() -> None:
    runtime = TuiRuntime()
    host = SimpleNamespace(
        lifecycle=ProcessLifecycle(),
        workspace_runtime=SimpleNamespace(
            coding=SimpleNamespace(
                exec_session_snapshots=AsyncMock(return_value=()),
                running_exec_sessions=AsyncMock(return_value={
                    "count": 2,
                    "items": [
                        {"origin": "tool", "session_id": "exec_model"},
                        {"origin": "tui_shell", "session_id": "shell_user"},
                    ],
                }),
                wait_exec_sessions_update=AsyncMock(
                    side_effect=asyncio.CancelledError(),
                ),
            ),
        ),
    )

    with (
        patch.object(
            runtime,
            "set_process_status_label",
            wraps=runtime.set_process_status_label,
        ) as set_process,
        patch.object(
            runtime,
            "set_user_shell_status_label",
            wraps=runtime.set_user_shell_status_label,
        ) as set_user_shell,
        patch.object(
            runtime,
            "set_background_shell_status_label",
            wraps=runtime.set_background_shell_status_label,
        ) as set_background_shell,
    ):
        with pytest.raises(asyncio.CancelledError):
            await monitor_exec_status(runtime, host)

    assert [call.args[0] for call in set_process.call_args_list] == [
        "1 background terminal running · /ps to view · /stop to close",
        "",
    ]
    assert [call.args[0] for call in set_user_shell.call_args_list] == [
        "1 background terminal running · /ps to view · /stop to close",
        "",
    ]
    assert [call.args[0] for call in set_background_shell.call_args_list] == [
        "2 background terminals running · /ps to view · /stop to close",
        "",
    ]


@pytest.mark.anyio
@pytest.mark.parametrize("exit_code", (0, 3))
async def test_process_completion_projects_once_without_tool_polling(exit_code) -> None:
    runtime = TuiRuntime()
    lifecycle = ProcessLifecycle()
    execution = ProcessSessionSnapshot(
        session_id="exec-original", cid="cid", sid="sid", turn_id="turn-old",
        call_id="call-original", command="print first and last", cwd=".",
        revision=1, output_lines=("first", "last"), dropped_lines=0,
        exit_code=exit_code, completed=True, elapsed_ms=100,
    )
    foreign = replace(execution, session_id="exec-foreign", sid="another-session")
    executions = AsyncMock(side_effect=[
        (replace(execution, completed=False, exit_code=None),),
        (execution, foreign),
        (execution, foreign),
    ])
    async def changed(**_kwargs):
        if executions.await_count == 3:
            lifecycle.stop_event.set()
        return {"changed": True, "revision": executions.await_count}

    wait_update = AsyncMock(side_effect=changed)
    host = SimpleNamespace(
        lifecycle=lifecycle,
        conversation=SimpleNamespace(cid="cid", sid="sid"),
        workspace_runtime=SimpleNamespace(coding=SimpleNamespace(
            running_exec_sessions=AsyncMock(return_value={"revision": 0, "items": []}),
            exec_session_snapshots=executions,
            wait_exec_sessions_update=wait_update,
        )),
    )
    with patch.object(runtime, "append_history_block", wraps=runtime.append_history_block) as append:
        await asyncio.wait_for(monitor_exec_status(runtime, host), timeout=2)
    append.assert_called_once()
    entry = append.call_args
    assert "Ran" in fragments_text(entry.args[0].fragments)
    assert "first" in entry.kwargs["raw_text"]
    assert "last" in entry.kwargs["raw_text"]
    assert entry.kwargs["source"].call_id == "call-original"
    assert entry.kwargs["source"].ok is (exit_code == 0)
    assert wait_update.await_args_list[0].kwargs["revision"] == 0


@pytest.mark.anyio
async def test_process_status_excludes_inline_shell_and_shows_background(
) -> None:
    runtime = TuiRuntime()
    process = runtime.begin_inline_process(
        "exec_current",
        FragmentBlock((("", "• Shell current"),)),
    )
    host = SimpleNamespace(
        lifecycle=ProcessLifecycle(),
        workspace_runtime=SimpleNamespace(
            coding=SimpleNamespace(
                exec_session_snapshots=AsyncMock(return_value=()),
                running_exec_sessions=AsyncMock(return_value={
                    "count": 2,
                    "items": [
                        {
                            "session_id": "exec_background",
                            "command": "ping -t 8.8.8.8",
                        },
                        {
                            "session_id": "exec_current",
                            "command": "adb devices",
                        },
                    ],
                }),
                wait_exec_sessions_update=AsyncMock(
                    side_effect=asyncio.CancelledError(),
                ),
            ),
        ),
    )

    with pytest.raises(asyncio.CancelledError):
        await monitor_exec_status(runtime, host)

    assert runtime.screen.process_status.label == ""

    runtime.resolve_inline_process("detach", session_id="exec_current")
    assert await process == "detach"
    runtime.dismiss_inline_process("exec_current")
