# -*- coding: utf-8 -*-

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from prompt_toolkit.utils import get_cwidth

from mind_app.tui.core.render import fragments_text
from mind_app.tui.core.runtime import TuiRuntime
from mind_app.tui.features.context import exec_status_display_label
from mind_app.tui.features.processes import monitor_exec_status


def test_process_status_is_a_dedicated_optional_row() -> None:
    runtime = TuiRuntime()

    assert runtime._process_status_height() == 0

    runtime.set_process_status_label("pytest -q · +2")

    assert runtime._process_status_height() == 1
    assert fragments_text(runtime.process_status.fragments()) == (
        "exec · pytest -q · +2"
    )
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

    assert label.endswith(" · +1")
    assert get_cwidth(f"exec · {label}") <= 20


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
