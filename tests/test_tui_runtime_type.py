# -*- coding: utf-8 -*-

from io import StringIO
from unittest.mock import AsyncMock

import pytest
from prompt_toolkit.output.plain_text import PlainTextOutput

from mind_app.frontend.contracts import PassiveFrontendRuntime
from mind_app.tui.core.models import FragmentBlock
from mind_app.tui.core.queued import TuiSubmission
from mind_app.tui.core.runtime import (
    TuiRuntime,
    require_tui_runtime
)
from mind_app.tui.core.styles import exit_summary_fragments


def test_require_tui_runtime_returns_concrete_runtime() -> None:
    runtime = TuiRuntime()

    assert require_tui_runtime(runtime) is runtime


def test_require_tui_runtime_rejects_passive_runtime() -> None:
    with pytest.raises(TypeError, match="TUI frontend requires TuiRuntime"):
        require_tui_runtime(PassiveFrontendRuntime())


def test_exit_summary_renders_as_plain_terminal_text() -> None:
    stdout = StringIO()
    runtime = TuiRuntime(output_obj=PlainTextOutput(stdout))

    runtime.print_exit_summary("sid_test_1_abcdef")

    assert stdout.getvalue() == (
        "\r\n■ To continue this session, run "
        "mind resume sid_test_1_abcdef\r\n"
    )


def test_exit_summary_command_is_bright_cyan_without_bold() -> None:
    fragments = exit_summary_fragments("sid_test_1_abcdef")
    command_style, command = fragments[-1]

    assert command == "mind resume sid_test_1_abcdef"
    assert command_style == "fg:#4DE3FF"
    assert "bold" not in command_style


@pytest.mark.anyio
async def test_runtime_close_erases_empty_canvas() -> None:
    runtime = TuiRuntime()
    runtime._exit_application = AsyncMock()

    await runtime.close()

    runtime._exit_application.assert_awaited_once_with(erase=True)


@pytest.mark.anyio
async def test_runtime_close_preserves_conversation_without_bottom_area() -> None:
    runtime = TuiRuntime()
    runtime.append_block(
        FragmentBlock((("class:user", "question"),)),
        kind="user",
    )
    runtime.append_block(
        FragmentBlock((("class:assistant", "answer"),)),
        kind="assistant",
    )
    runtime.submissions.queued_messages.append(TuiSubmission(
        value="queued",
        editable_text="queued",
        paste_store={},
    ))
    runtime._exit_application = AsyncMock()

    await runtime.close()

    runtime._exit_application.assert_awaited_once_with(erase=False)
    assert not runtime.screen.input_area.filter()
    assert not runtime.screen._footer_visible()
    assert runtime.screen._interaction_height() == 0
    assert runtime.screen._content_input_gap_height() == 0
    assert runtime.screen._queued_height() == 0
