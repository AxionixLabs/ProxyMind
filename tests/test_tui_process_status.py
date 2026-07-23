# -*- coding: utf-8 -*-

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from prompt_toolkit.utils import get_cwidth

from mind_app.tui.core.process_status import TuiProcessStatus
from mind_app.tui.core.render import fragments_text
from mind_app.tui.core.runtime import TuiRuntime
from mind_app.tui.features.context import exec_status_display_label
from mind_app.tui.features.processes import monitor_exec_status
from mind_app.tui.features.summary import (
    CommandSummary,
    command_summary_title_parts,
)


def test_process_status_is_a_dedicated_optional_row() -> None:
    runtime = TuiRuntime()

    assert runtime._process_status_height() == 0

    runtime.set_process_status_label("pytest -q · +2")

    assert runtime._process_status_height() == 1
    rendered = fragments_text(runtime.process_status.fragments())
    assert rendered[0] in {"◦", "•"}
    assert rendered[1:] == " exec pytest -q · +2 · /ps to view"
    assert "pytest -q" not in fragments_text(runtime._footer_fragments())
    assert runtime.canvas.children.index(runtime.status_window) < (
        runtime.canvas.children.index(runtime.process_status_window)
    ) < runtime.canvas.children.index(runtime.queued_window)

    runtime.set_process_status_label("")

    assert runtime._process_status_height() == 0


def test_process_status_summary_respects_terminal_display_width() -> None:
    label = exec_status_display_label(
        {
            "count": 2,
            "items": [{"command": "测试测试测试测试测试 command"}],
        },
        line_width=20,
    )

    status = TuiRuntime().process_status
    status._get_width = lambda: 20
    status.set_label(label)
    rendered = fragments_text(status.fragments())

    assert get_cwidth(rendered) <= 20
    assert "/ps to view" in rendered


def test_process_status_remains_static() -> None:
    status = TuiProcessStatus(
        invalidate=lambda: None,
        get_width=lambda: 80,
    )

    status.set_label("adb logcat · +2")
    first = status.fragments()
    second = status.fragments()

    assert fragments_text(first) == "• exec adb logcat · +2 · /ps to view"
    assert second == first


def test_command_summary_bolds_action_but_not_command() -> None:
    parts = command_summary_title_parts(CommandSummary(
        kind="Started",
        command="adb logcat",
    ))

    assert any("bold" in style and text == "Started" for text, style in parts)
    assert any("bold" not in style and text == "adb logcat" for text, style in parts)


@pytest.mark.anyio
async def test_process_status_monitor_updates_and_clears_runtime() -> None:
    runtime = TuiRuntime()
    mind = SimpleNamespace(
        task_event=SimpleNamespace(is_set=lambda: False),
        native_coding=SimpleNamespace(
            running_exec_sessions=AsyncMock(return_value={
                "count": 1,
                "items": [{"command": "pytest -q"}],
            }),
        ),
    )

    with (
        patch.object(
            runtime,
            "set_process_status_label",
            wraps=runtime.set_process_status_label,
        ) as set_label,
        patch(
            "mind_app.tui.features.processes.asyncio.sleep",
            AsyncMock(side_effect=asyncio.CancelledError()),
        ),
    ):
        with pytest.raises(asyncio.CancelledError):
            await monitor_exec_status(runtime, mind)

    mind.native_coding.running_exec_sessions.assert_awaited_once()
    assert [call.args[0] for call in set_label.call_args_list] == [
        "pytest -q",
        "",
    ]
    assert runtime.process_status.label == ""
