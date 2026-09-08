# -*- coding: utf-8 -*-

"""验证完整 transcript 阅读器、超链接与长会话虚拟化。

这些场景共享同一虚拟视口状态，维持整体可保留滚动、焦点和链接的联动约束。
"""


import asyncio
import io
from copy import deepcopy
from unittest.mock import (
    AsyncMock,
    Mock,
    PropertyMock,
    call,
    patch,
)
import pytest
from prompt_toolkit.data_structures import Size
from prompt_toolkit.input.defaults import create_pipe_input
from prompt_toolkit.output import DummyOutput
from prompt_toolkit.output.vt100 import Vt100_Output
from prompt_toolkit.utils import get_cwidth
from frontends.terminal.capabilities import (
    TerminalCapabilities,
    TerminalTheme,
)
from frontends.terminal.color_support import (
    TerminalColorLevel,
    TerminalColorSupport,
)
from frontends.terminal.identity import (
    TerminalIdentity,
    TerminalKind,
)
from frontends.interaction.contracts import PromptContext
from frontends.terminal.text import sanitize_terminal_text
from agent.application.views.builders.tools import (
    build_generic_tool_result_view,
    build_native_tool_result_view,
    build_tool_start_view,
)
from frontends.tui.adapters.markdown import (
    TuiMarkdownStreamRenderer,
    render_tui_assistant_markdown,
    render_tui_markdown,
)
from frontends.tui.adapters.output import TuiOutputControl
from frontends.tui.adapters.presentation import TuiPresentationSink
from frontends.tui.core.document import (
    TranscriptBlock,
    TuiBlockKind,
    TuiDocument,
)
from frontends.tui.core.models import (
    FragmentBlock,
    LineFill,
    MenuOption,
    MenuRequest,
)
from frontends.tui.core.hyperlinks import (
    OSC8_CLOSE,
    decorate_scrollback_hyperlinks,
    terminal_hyperlink_from_style,
    terminal_hyperlink_style
)
from frontends.tui.core.render import (
    display_line_count,
    fragment_continuation_widths,
    fragments_text,
    join_formatted_lines,
    sanitize_formatted_text,
    split_formatted_lines,
    wrap_formatted_lines,
)
from frontends.tui.core.runtime import TuiRuntime
from frontends.tui.core.styles import (
    ASSISTANT_PREFIX_CLASS,
    assistant_block,
    failure_parts,
    query_block,
)
from tests.frontends.tui.rendering.frame_scenarios import (
    AlternateScreenOutput as _AlternateScreenOutput,
    block as _block,
    document_text as _document_text,
    render_next_frame as _render_next_frame,
)


@pytest.mark.anyio
async def test_ctrl_l_repeatedly_hides_new_transcript_without_losing_archive() -> None:
    with create_pipe_input() as pipe_input:
        runtime = TuiRuntime(input_obj=pipe_input, output_obj=DummyOutput())

        with patch.object(
            runtime.screen.application.output,
            "get_size",
            return_value=Size(rows=12, columns=40),
        ):
            await runtime.open()
            try:
                runtime.append_block(_block("previous answer"), kind="assistant")
                runtime.screen.input.buffer.text = "draft input"
                pipe_input.send_text("\x0c")

                for _ in range(100):
                    await asyncio.sleep(0.01)
                    if runtime.document.cleared_line_count == 1:
                        break

                assert not runtime.document.has_visible_content
                assert runtime.document.cleared_line_count == 1
                assert runtime.screen.input.buffer.text == "draft input"

                runtime.append_block(_block("new answer"), kind="assistant")
                await _render_next_frame(runtime)

                pipe_input.send_text("\x0c")
                for _ in range(100):
                    await asyncio.sleep(0.01)
                    if runtime.document.cleared_line_count == 3:
                        break

                assert not runtime.document.has_visible_content
                assert runtime.document.cleared_line_count == 3
                assert runtime.screen.input.buffer.text == "draft input"
                assert "previous answer" in "".join(
                    text
                    for _style, text in runtime.document.all_fragments(width=40)
                )
                assert "new answer" in "".join(
                    text
                    for _style, text in runtime.document.all_fragments(width=40)
                )
            finally:
                await runtime.close()


@pytest.mark.anyio
async def test_ctrl_l_clear_discards_full_canvas_height() -> None:
    with create_pipe_input() as pipe_input:
        runtime = TuiRuntime(input_obj=pipe_input, output_obj=DummyOutput())

        with patch.object(
            runtime.screen.application.output,
            "get_size",
            return_value=Size(rows=12, columns=40),
        ):
            await runtime.open()
            try:
                runtime.append_block(
                    _block("\n".join(f"line {index}" for index in range(30))),
                    kind="assistant",
                )
                await _render_next_frame(runtime)

                assert runtime.screen._visible_height() == (
                    runtime.screen._natural_visible_height()
                )

                pipe_input.send_text("\x0c")
                for _ in range(100):
                    await asyncio.sleep(0.01)
                    if not runtime.document.has_visible_content:
                        break
                else:
                    raise AssertionError("Ctrl+L did not clear the transcript")

                screen = await _render_next_frame(runtime)
                positions = screen.visible_windows_to_write_positions
                outer_inset = positions[
                    runtime.screen.bottom_pane_top_inset.content
                ]
                top_padding = positions[runtime.screen.input_top_padding]
                input_position = positions[runtime.screen.input.window]

                assert runtime.screen._visible_height() == (
                    runtime.screen._natural_visible_height()
                )
                assert runtime.screen.canvas_spacer not in positions
                assert outer_inset.ypos == 0
                assert outer_inset.height == 1
                assert top_padding.ypos == outer_inset.ypos + 1
                assert input_position.ypos == top_padding.ypos + 1
            finally:
                await runtime.close()


@pytest.mark.anyio
async def test_ctrl_t_opens_and_closes_full_transcript_overlay() -> None:
    with create_pipe_input() as pipe_input:
        output = _AlternateScreenOutput(columns=72, rows=18)
        runtime = TuiRuntime(input_obj=pipe_input, output_obj=output)
        runtime.append_block(
            query_block("first question"),
            kind="user",
        )
        runtime.append_block(
            _block("compact"),
            kind="operation",
            transcript_block=_block(
                "full output\n"
                + "\n".join(f"line {index}" for index in range(30))
            ),
        )

        await runtime.open()
        try:
            pipe_input.send_text("\x14")
            for _ in range(100):
                await asyncio.sleep(0.01)
                if runtime.screen.transcript_overlay.active:
                    break

            assert runtime.screen.transcript_overlay.active
            for _ in range(100):
                await asyncio.sleep(0.01)
                if output.enter_count:
                    break

            assert output.enter_count == 1
            assert runtime.screen.application.full_screen
            assert runtime.screen.application.renderer.full_screen
            assert runtime.screen.application.renderer.last_rendered_screen is not None
            assert runtime.screen.application.renderer.last_rendered_screen.height == 18
            rendered_screen = runtime.screen.application.renderer.last_rendered_screen
            rendered_text = "\n".join(
                "".join(
                    rendered_screen.data_buffer[row][column].char
                    for column in range(72)
                )
                for row in range(18)
            )
            assert "T R A N S C R I P T" in rendered_text
            rendered_lines = rendered_text.splitlines()
            assert rendered_lines[0].startswith(
                "/ T R A N S C R I P T"
            )
            assert "line 29" in rendered_text
            assert "Esc/Q/Ctrl+C/Ctrl+T to quit" in rendered_text
            assert runtime.screen.transcript_overlay.scroll_offset > 0
            assert runtime.screen.transcript_overlay.follow_bottom
            assert runtime.screen.transcript_overlay.scroll_percentage() == 100
            assert any(
                "─" in line and " 100% " in line
                for line in rendered_lines
            )
            assert "full output" in "".join(
                text
                for _style, text in runtime.screen.transcript_overlay.fragments()
            )
            assert runtime.screen.application.layout.current_control == (
                runtime.screen.transcript_overlay_control
            )

            pipe_input.send_text("\x1b")
            for _ in range(100):
                await asyncio.sleep(0.01)
                if not runtime.screen.transcript_overlay.active:
                    break

            assert not runtime.screen.transcript_overlay.active
            assert output.quit_count == 1
            assert not runtime.screen.application.full_screen
            assert not runtime.screen.application.renderer.full_screen
            assert runtime.screen.application.layout.current_control == (
                runtime.screen.input.control
            )
        finally:
            await runtime.close()


@pytest.mark.anyio
async def test_ctrl_t_over_inline_process_tracks_active_output() -> None:
    with create_pipe_input() as pipe_input:
        runtime = TuiRuntime(input_obj=pipe_input, output_obj=DummyOutput())
        await runtime.open()
        try:
            process = runtime.begin_inline_process(
                "exec_shell",
                _block("running"),
                transcript_block=_block("$ command\nlive output"),
            )
            assert runtime.screen.application.layout.current_control == (
                runtime.screen.input.control
            )

            pipe_input.send_text("\x14")
            for _ in range(100):
                await asyncio.sleep(0.01)
                if runtime.screen.transcript_overlay.active:
                    break

            assert runtime.screen.transcript_overlay.active
            assert runtime.screen.application.layout.current_control == (
                runtime.screen.transcript_overlay_control
            )
            assert "$ command\nlive output" in "".join(
                text
                for _style, text in runtime.screen.transcript_overlay.fragments()
            )

            runtime.update_inline_process(
                _block("running"),
                session_id="exec_shell",
                transcript_block=_block("$ command\nlive output\nnext line"),
            )
            assert "next line" in "".join(
                text
                for _style, text in runtime.screen.transcript_overlay.fragments()
            )

            pipe_input.send_text("\x1b")
            for _ in range(100):
                await asyncio.sleep(0.01)
                if not runtime.screen.transcript_overlay.active:
                    break

            assert runtime.screen.application.layout.current_control == (
                runtime.screen.input.control
            )

            runtime.resolve_inline_process("done", session_id="exec_shell")
            assert await process == "done"
            runtime.commit_inline_process(
                _block("completed"),
                session_id="exec_shell",
                transcript_block=_block("$ command\ncomplete output"),
            )
            assert runtime.screen.application.layout.current_control == (
                runtime.screen.input.control
            )
        finally:
            await runtime.close()


@pytest.mark.anyio
async def test_inline_process_completion_keeps_transcript_screen_focused() -> None:
    with create_pipe_input() as pipe_input:
        runtime = TuiRuntime(input_obj=pipe_input, output_obj=DummyOutput())
        await runtime.open()
        try:
            process = runtime.begin_inline_process(
                "exec_shell",
                _block("running"),
                transcript_block=_block("$ command\nlive output"),
            )
            runtime.toggle_transcript_overlay()

            runtime.resolve_inline_process("done", session_id="exec_shell")
            assert await process == "done"
            runtime.commit_inline_process(
                _block("completed"),
                session_id="exec_shell",
                transcript_block=_block("$ command\ncomplete output"),
            )

            assert runtime.screen.transcript_overlay.active
            assert runtime.screen.application.layout.current_control == (
                runtime.screen.transcript_overlay_control
            )
            assert "complete output" in "".join(
                text
                for _style, text in runtime.screen.transcript_overlay.fragments()
            )

            runtime.toggle_transcript_overlay()
            assert runtime.screen.application.layout.current_control == (
                runtime.screen.input.control
            )
        finally:
            await runtime.close()


@pytest.mark.parametrize("surface", ["approval", "menu"])
def test_transcript_overlay_does_not_cover_blocking_surface(surface: str) -> None:
    runtime = TuiRuntime()
    runtime.screen.bottom_pane.activate(surface)

    with patch.object(
        runtime.viewport,
        "pause_scrollback",
    ) as pause_scrollback, patch.object(
        runtime.viewport,
        "schedule_scrollback_flush",
    ) as resume_scrollback:
        runtime.toggle_transcript_overlay()

    assert not runtime.screen.transcript_overlay.active
    assert runtime.screen.bottom_pane.active_surface == surface
    pause_scrollback.assert_called_once_with()
    resume_scrollback.assert_called_once_with()


def test_transcript_open_pauses_scrollback_before_switching_screen() -> None:
    runtime = TuiRuntime()
    calls: list[str] = []

    with patch.object(
        runtime.viewport,
        "pause_scrollback",
        side_effect=lambda: calls.append("pause"),
    ), patch.object(
        runtime.screen,
        "set_transcript_overlay",
        side_effect=lambda active: calls.append(f"overlay:{active}") or True,
    ):
        runtime.toggle_transcript_overlay()

    assert calls == ["pause", "overlay:True"]


def test_transcript_screen_immediately_owns_terminal_from_origin() -> None:
    output = _AlternateScreenOutput(columns=64, rows=16)
    runtime = TuiRuntime(output_obj=output)
    renderer = runtime.screen.application.renderer
    calls: list[object] = []

    with patch.object(
        output,
        "enter_alternate_screen",
        side_effect=lambda: calls.append("enter"),
    ), patch.object(
        output,
        "erase_screen",
        side_effect=lambda: calls.append("erase"),
    ), patch.object(
        output,
        "cursor_goto",
        side_effect=lambda row, column: calls.append(("cursor", row, column)),
    ), patch.object(
        output,
        "flush",
        side_effect=lambda: calls.append("flush"),
    ):
        assert runtime.screen.set_transcript_overlay(True)

    assert calls == ["enter", "erase", ("cursor", 0, 0), "flush"]
    assert renderer._in_alternate_screen


def test_transcript_screen_pairs_vt_terminal_modes(monkeypatch) -> None:
    stream = io.StringIO()
    output = Vt100_Output(
        stream,
        lambda: Size(rows=16, columns=64),
        term="xterm-256color",
        enable_cpr=False,
    )
    monkeypatch.setattr("frontends.tui.core.screen.sys.platform", "win32")
    runtime = TuiRuntime(output_obj=output)

    runtime.toggle_transcript_overlay()
    runtime.toggle_transcript_overlay()

    terminal_output = stream.getvalue()
    assert "\x1b[?1049h\x1b[H\x1b[?1007h\x1b[2J\x1b[0;0H" in (
        terminal_output
    )
    assert "\x1b[?1007l\x1b[?1049l" in terminal_output


def test_transcript_screen_reopens_at_latest_content() -> None:
    runtime = TuiRuntime()
    runtime.screen._output_size = lambda: (40, 10)
    runtime.append_block(query_block("first question"), kind="user")
    runtime.append_block(
        _block("\n".join(f"line {index}" for index in range(20))),
        kind="assistant",
    )
    overlay = runtime.screen.transcript_overlay

    runtime.toggle_transcript_overlay()
    overlay.jump_top()
    runtime.toggle_transcript_overlay()
    runtime.toggle_transcript_overlay()

    assert overlay.scroll_offset == overlay._max_scroll_offset()
    assert overlay.follow_bottom
    assert fragments_text(overlay.visible_fragments()).endswith("line 19")


def test_transcript_takeover_failure_restores_inline_screen() -> None:
    output = _AlternateScreenOutput(columns=64, rows=16)
    runtime = TuiRuntime(output_obj=output)
    screen = runtime.screen

    with patch.object(
        output,
        "erase_screen",
        side_effect=OSError("terminal unavailable"),
    ), pytest.raises(OSError, match="terminal unavailable"):
        screen.set_transcript_overlay(True)

    assert output.enter_count == 1
    assert output.quit_count == 1
    assert not screen.transcript_overlay.active
    assert not screen.application.full_screen
    assert not screen.application.renderer.full_screen
    assert not screen.application.renderer._in_alternate_screen
    assert screen._inline_renderer_state is None


def test_transcript_open_failure_restores_inline_renderer_state() -> None:
    runtime = TuiRuntime()
    screen = runtime.screen
    renderer = screen.application.renderer
    original_state = (
        renderer._cursor_pos,
        renderer._last_screen,
        renderer._last_size,
        renderer._last_style,
        renderer._last_cursor_shape,
        renderer._min_available_height,
    )

    with patch.object(
        screen.transcript_overlay,
        "_invalidate",
        side_effect=RuntimeError("render failed"),
    ), pytest.raises(RuntimeError, match="render failed"):
        screen.set_transcript_overlay(True)

    assert not screen.transcript_overlay.active
    assert not screen.application.full_screen
    assert not renderer.full_screen
    assert not renderer._in_alternate_screen
    assert screen._inline_renderer_state is None
    assert screen.application.layout.current_control == screen.input.control
    assert (
        renderer._cursor_pos,
        renderer._last_screen,
        renderer._last_size,
        renderer._last_style,
        renderer._last_cursor_shape,
        renderer._min_available_height,
    ) == original_state


def test_transcript_enter_failure_restores_inline_renderer_state() -> None:
    runtime = TuiRuntime()
    screen = runtime.screen
    renderer = screen.application.renderer
    original_cursor = renderer._cursor_pos

    with patch.object(
        type(screen),
        "terminal_height",
        new_callable=PropertyMock,
        side_effect=OSError("terminal unavailable"),
    ), pytest.raises(OSError, match="terminal unavailable"):
        screen.set_transcript_overlay(True)

    assert not screen.transcript_overlay.active
    assert not screen.application.full_screen
    assert not renderer.full_screen
    assert not renderer._in_alternate_screen
    assert renderer._cursor_pos == original_cursor
    assert screen._inline_renderer_state is None
    assert screen.application.layout.current_control == screen.input.control


def test_transcript_close_failure_still_leaves_full_screen() -> None:
    runtime = TuiRuntime()
    screen = runtime.screen
    renderer = screen.application.renderer
    assert screen.set_transcript_overlay(True)

    with patch.object(
        screen.transcript_overlay,
        "_invalidate",
        side_effect=RuntimeError("render failed"),
    ), pytest.raises(RuntimeError, match="render failed"):
        screen.set_transcript_overlay(False)

    assert not screen.transcript_overlay.active
    assert not screen.application.full_screen
    assert not renderer.full_screen
    assert screen._inline_renderer_state is None
    assert screen.application.layout.current_control == screen.input.control


def test_transcript_leave_restores_state_after_output_failure() -> None:
    runtime = TuiRuntime()
    screen = runtime.screen
    renderer = screen.application.renderer
    original_cursor = renderer._cursor_pos

    screen._enter_full_screen_overlay()
    renderer._in_alternate_screen = True

    with patch.object(
        renderer.output,
        "quit_alternate_screen",
        side_effect=OSError("terminal unavailable"),
    ), pytest.raises(OSError, match="terminal unavailable"):
        screen._leave_full_screen_overlay()

    assert not screen.application.full_screen
    assert not renderer.full_screen
    assert not renderer._in_alternate_screen
    assert renderer._cursor_pos == original_cursor
    assert screen._inline_renderer_state is None


@pytest.mark.anyio
async def test_runtime_close_leaves_active_transcript_screen() -> None:
    with create_pipe_input() as pipe_input:
        output = _AlternateScreenOutput(columns=64, rows=16)
        runtime = TuiRuntime(input_obj=pipe_input, output_obj=output)
        await runtime.open()

        runtime.toggle_transcript_overlay()
        for _ in range(100):
            await asyncio.sleep(0.01)
            if output.enter_count:
                break

        assert output.enter_count == 1

        await runtime.close()

        assert output.quit_count == 1
        assert not runtime.screen.transcript_overlay.active
        assert not runtime.screen.application.renderer.full_screen


@pytest.mark.anyio
async def test_transcript_screen_repeatedly_restores_inline_renderer() -> None:
    with create_pipe_input() as pipe_input:
        output = _AlternateScreenOutput(columns=64, rows=16)
        runtime = TuiRuntime(input_obj=pipe_input, output_obj=output)
        runtime.append_block(_block("conversation"), kind="assistant")
        await runtime.open()
        try:
            for cycle in range(1, 101):
                output.size = Size(
                    rows=15 + cycle,
                    columns=60 + cycle * 3,
                )
                runtime.toggle_transcript_overlay()
                for _ in range(100):
                    await asyncio.sleep(0.01)
                    screen = runtime.screen.application.renderer.last_rendered_screen
                    if (
                        output.enter_count == cycle
                        and screen is not None
                        and screen.height == output.size.rows
                    ):
                        break

                assert runtime.screen.transcript_overlay.active
                assert runtime.screen.application.full_screen
                assert runtime.screen.application.renderer.full_screen
                assert output.enter_count == cycle

                runtime.toggle_transcript_overlay()
                for _ in range(100):
                    await asyncio.sleep(0.01)
                    if (
                        output.quit_count == cycle
                        and not runtime.screen.application.renderer.full_screen
                    ):
                        break

                assert not runtime.screen.transcript_overlay.active
                assert not runtime.screen.application.full_screen
                assert not runtime.screen.application.renderer.full_screen
                assert runtime.screen._inline_renderer_state is None
                assert output.quit_count == cycle
        finally:
            await runtime.close()

    assert output.enter_count == 100
    assert output.quit_count == 100


@pytest.mark.anyio
async def test_transcript_screen_tracks_terminal_resize() -> None:
    with create_pipe_input() as pipe_input:
        output = _AlternateScreenOutput(columns=60, rows=14)
        runtime = TuiRuntime(input_obj=pipe_input, output_obj=output)
        runtime.append_block(
            _block("compact"),
            kind="operation",
            transcript_block=_block("full output"),
        )
        await runtime.open()
        try:
            runtime.toggle_transcript_overlay()
            for _ in range(100):
                await asyncio.sleep(0.01)
                screen = runtime.screen.application.renderer.last_rendered_screen
                if output.enter_count and screen is not None and screen.height == 14:
                    break

            output.size = Size(rows=21, columns=88)
            runtime.invalidate()
            for _ in range(100):
                await asyncio.sleep(0.01)
                screen = runtime.screen.application.renderer.last_rendered_screen
                if screen is not None and screen.height == 21:
                    break

            screen = runtime.screen.application.renderer.last_rendered_screen
            assert screen is not None
            assert screen.height == 21
            assert runtime.screen.application.renderer._last_size == output.size
            assert runtime.screen._transcript_overlay_height() == 16
        finally:
            await runtime.close()


@pytest.mark.anyio
async def test_transcript_screen_supports_codex_pager_keys() -> None:
    with create_pipe_input() as pipe_input:
        output = _AlternateScreenOutput(columns=40, rows=10)
        runtime = TuiRuntime(input_obj=pipe_input, output_obj=output)
        runtime.append_block(
            _block("compact"),
            kind="operation",
            transcript_block=_block(
                "\n".join(f"line {index}" for index in range(30))
            ),
        )
        await runtime.open()
        try:
            pipe_input.send_text("\x14")
            for _ in range(100):
                await asyncio.sleep(0.01)
                if runtime.screen.transcript_overlay.active:
                    break

            runtime.screen.transcript_overlay.jump_top()
            pipe_input.send_text(" ")
            for _ in range(100):
                await asyncio.sleep(0.01)
                if runtime.screen.transcript_overlay.scroll_offset == 5:
                    break

            assert runtime.screen.transcript_overlay.scroll_offset == 5
            assert not runtime.screen.transcript_overlay.follow_bottom

            pipe_input.send_text("\x02")
            for _ in range(100):
                await asyncio.sleep(0.01)
                if runtime.screen.transcript_overlay.scroll_offset == 0:
                    break

            assert runtime.screen.transcript_overlay.scroll_offset == 0

            pipe_input.send_text("j")
            for _ in range(100):
                await asyncio.sleep(0.01)
                if runtime.screen.transcript_overlay.scroll_offset == 1:
                    break

            assert runtime.screen.transcript_overlay.scroll_offset == 1

            pipe_input.send_text("k")
            for _ in range(100):
                await asyncio.sleep(0.01)
                if runtime.screen.transcript_overlay.scroll_offset == 0:
                    break

            assert runtime.screen.transcript_overlay.scroll_offset == 0

            pipe_input.send_text("\x06")
            for _ in range(100):
                await asyncio.sleep(0.01)
                if runtime.screen.transcript_overlay.scroll_offset == 5:
                    break

            assert runtime.screen.transcript_overlay.scroll_offset == 5

            runtime.screen.transcript_overlay.scroll_half_page(1)
            assert runtime.screen.transcript_overlay.scroll_offset == 8

            runtime.screen.transcript_overlay.jump_bottom()

            assert runtime.screen.transcript_overlay.follow_bottom
            assert runtime.screen.transcript_overlay.scroll_percentage() == 100
        finally:
            await runtime.close()


@pytest.mark.anyio
async def test_transcript_screen_uses_compact_chrome_in_short_terminal() -> None:
    with create_pipe_input() as pipe_input:
        output = _AlternateScreenOutput(columns=20, rows=6)
        runtime = TuiRuntime(input_obj=pipe_input, output_obj=output)
        runtime.append_block(
            _block("compact"),
            kind="operation",
            transcript_block=_block("full output"),
        )
        await runtime.open()
        try:
            runtime.toggle_transcript_overlay()
            for _ in range(100):
                await asyncio.sleep(0.01)
                screen = runtime.screen.application.renderer.last_rendered_screen
                if output.enter_count and screen is not None and screen.height == 6:
                    break

            screen = runtime.screen.application.renderer.last_rendered_screen
            assert screen is not None
            rendered_text = "\n".join(
                "".join(
                    screen.data_buffer[row][column].char
                    for column in range(20)
                )
                for row in range(6)
            )
            assert "full output" in rendered_text
            assert output.enter_count == 1
        finally:
            await runtime.close()


def test_transcript_overlay_preserves_reader_position_during_active_updates() -> None:
    runtime = TuiRuntime()
    runtime.screen._output_size = lambda: (40, 10)
    runtime.append_block(
        _block("compact"),
        kind="operation",
        transcript_block=_block("\n".join(f"line {index}" for index in range(30))),
    )
    runtime.toggle_transcript_overlay()
    runtime.screen.transcript_overlay.scroll_page(-1)
    scroll_offset = runtime.screen.transcript_overlay.scroll_offset

    runtime.set_active_renderable(
        _block("running"),
        kind="operation",
        transcript_block=_block("$ command\nlatest output"),
    )

    assert runtime.screen.transcript_overlay.scroll_offset == scroll_offset
    assert not runtime.screen.transcript_overlay.follow_bottom
    assert "latest output" in "".join(
        text for _style, text in runtime.screen.transcript_overlay.fragments()
    )

    runtime.screen.transcript_overlay.jump_bottom()
    assert runtime.screen.transcript_overlay.follow_bottom


def test_transcript_overlay_reuses_stable_cache_during_active_updates() -> None:
    runtime = TuiRuntime()
    runtime.append_block(_block("stable"), kind="assistant")
    overlay = runtime.screen.transcript_overlay
    stable_cell = runtime.document.blocks[0]

    with patch.object(
        runtime.document,
        "transcript_cell_fragments",
        wraps=runtime.document.transcript_cell_fragments,
    ) as render_cell:
        runtime.toggle_transcript_overlay()
        assert "".join(text for _style, text in overlay.fragments()) == "stable"

        runtime.set_active_renderable(
            _block("running"),
            kind="operation",
            transcript_block=_block("$ command\nfirst"),
        )
        first = "".join(text for _style, text in overlay.fragments())

        runtime.set_active_renderable(
            _block("running"),
            kind="operation",
            transcript_block=_block("$ command\nfirst\nsecond"),
        )
        second = "".join(text for _style, text in overlay.fragments())

    stable_renders = sum(
        call.args[0] is stable_cell
        for call in render_cell.call_args_list
    )
    assert stable_renders == 1
    assert first == "stable\n\n$ command\nfirst"
    assert second == "stable\n\n$ command\nfirst\nsecond"


def test_main_transcript_switches_between_rich_and_raw_cells() -> None:
    runtime = TuiRuntime()
    runtime.append_block(
        _block("compact"),
        kind="operation",
        transcript_block=_block("Ran shell_command\n$ npm install"),
        raw_text="npm install",
    )
    assert "".join(
        text for _style, text in runtime.document.fragments(width=80)
    ) == "compact"

    runtime.set_raw_output_mode(True)

    raw = runtime.document.fragments(width=80)
    assert runtime.document.raw_output_mode
    assert "".join(text for _style, text in raw) == "npm install"
    assert all(not style for style, _text in raw)

    runtime.set_raw_output_mode(False)

    assert not runtime.document.raw_output_mode
    assert "".join(
        text for _style, text in runtime.document.fragments(width=80)
    ) == "compact"


@pytest.mark.anyio
async def test_main_transcript_keeps_stream_source_text_for_raw_mode() -> None:
    runtime = TuiRuntime()
    output = TuiOutputControl("", runtime=runtime, animate=False)

    await output.append_assistant_delta("**bold** and `code`")
    await output.prepare_external_output()
    rich = "".join(
        text for _style, text in runtime.document.fragments(width=80)
    )
    runtime.set_raw_output_mode(True)
    raw = "".join(
        text for _style, text in runtime.document.fragments(width=80)
    )

    assert "**" not in rich
    assert "`" not in rich
    assert raw == "**bold** and `code`"


@pytest.mark.anyio
async def test_markdown_hyperlink_degrades_safely_in_dynamic_tui() -> None:
    capabilities = TerminalCapabilities(
        TerminalIdentity(TerminalKind.ITERM2, "iTerm2"),
        TerminalColorSupport.fixed(TerminalColorLevel.TRUECOLOR),
    )
    runtime = TuiRuntime(terminal_capabilities=capabilities)
    runtime.screen._output_size = lambda: (20, 10)
    output = TuiOutputControl("", runtime=runtime, animate=False)

    await output.append_assistant_delta(
        "[documentation-link-that-wraps](https://example.com/docs)"
    )
    await output.prepare_external_output()

    cell_fragments = runtime.document.blocks[-1].display_block.fragments
    cell_lines = fragments_text(cell_fragments).splitlines()
    assert cell_lines[0].startswith("• ")
    assert all(line.startswith("  ") for line in cell_lines[1:])
    assert "".join(line[2:] for line in cell_lines) == (
        "documentation-link-that-wraps"
    )
    assert "".join(
        text
        for style, text in cell_fragments
        if "underline" in style
    ) == "documentation-link-that-wraps"
    assert all(
        style != "[ZeroWidthEscape]"
        for style, _text in cell_fragments
    )
    assert all(
        style != "[ZeroWidthEscape]"
        for style, _text in runtime.document.scrollback_prefix_fragments(1)
    )

    runtime.toggle_transcript_overlay()
    overlay = runtime.screen.transcript_overlay
    rich = overlay.fragments()

    assert len(split_formatted_lines(rich)) > 1
    assert fragments_text(rich).replace("\n  ", "") == (
        "• documentation-link-that-wraps"
    )
    assert all(style != "[ZeroWidthEscape]" for style, _text in rich)

    runtime.toggle_transcript_overlay()
    runtime.set_raw_output_mode(True)
    raw = runtime.document.fragments(width=20)

    assert fragments_text(raw).replace("\n", "") == (
        "[documentation-link-that-wraps](https://example.com/docs)"
    )
    assert all(style != "[ZeroWidthEscape]" for style, _text in raw)


def test_markdown_hyperlink_metadata_survives_wrap_and_clip() -> None:
    block = render_tui_assistant_markdown(
        "[中文链接](https://example.com/docs) plain",
        8,
        hyperlinks=True,
    )

    rows = wrap_formatted_lines(list(block.fragments), width=4)
    linked = [
        (style, text)
        for row in rows
        for style, text in row
        if terminal_hyperlink_from_style(style)
    ]

    assert fragments_text(block.fragments) == "• 中文链\n  接\n  plain"
    assert "".join(text for _style, text in linked) == "中文链接"
    assert {
        terminal_hyperlink_from_style(style)
        for style, _text in linked
    } == {"https://example.com/docs"}
    assert all(
        terminal_hyperlink_from_style(style) is None
        for row in rows
        for style, text in row
        if "plain" in text
    )
    assert all(
        style != "[ZeroWidthEscape]"
        for style, _text in block.fragments
    )
    copied_links = [
        terminal_hyperlink_from_style(style)
        for style, _text in deepcopy(block).fragments
        if terminal_hyperlink_from_style(style)
    ]
    assert copied_links
    assert set(copied_links) == {"https://example.com/docs"}


def test_terminal_hyperlink_style_is_an_immutable_cache_key() -> None:
    style = terminal_hyperlink_style(
        "class:markdown.link",
        "https://example.com/docs",
    )
    cache = {style: "cached"}

    with pytest.raises(AttributeError):
        setattr(style, "destination", "https://example.com/changed")

    assert cache[style] == "cached"
    assert terminal_hyperlink_from_style(style) == "https://example.com/docs"


def test_scrollback_hyperlinks_close_each_visible_fragment() -> None:
    block = render_tui_markdown(
        "[docs](https://example.com/docs) tail",
        hyperlinks=True,
    )

    decorated = decorate_scrollback_hyperlinks(list(block.fragments))
    opens = [
        text
        for style, text in decorated
        if style == "[ZeroWidthEscape]" and text != OSC8_CLOSE
    ]
    closes = [
        text
        for style, text in decorated
        if style == "[ZeroWidthEscape]" and text == OSC8_CLOSE
    ]

    assert len(opens) == len(closes) == 1
    assert fragments_text(decorated) == "docs tail"
    assert terminal_hyperlink_from_style(decorated[-1][0]) is None


def test_scrollback_hyperlink_output_is_balanced() -> None:
    stream = io.StringIO()
    output = Vt100_Output(
        stream,
        lambda: Size(rows=12, columns=20),
        term="xterm-256color",
        enable_cpr=False,
    )
    runtime = TuiRuntime(output_obj=output)
    block = render_tui_markdown(
        "[docs](https://example.com/docs) tail",
        hyperlinks=True,
    )

    runtime.viewport._print_scrollback_fragments(list(block.fragments))

    payload = stream.getvalue()
    opening = "\x1b]8;;https://example.com/docs\x1b\\"
    assert payload.count(opening) == payload.count(OSC8_CLOSE) == 1
    assert sanitize_terminal_text(payload) == "docs tail\n"


def test_markdown_link_style_does_not_extend_to_following_text() -> None:
    block = render_tui_markdown(
        "终点：[回到家乡](home)\n\n"
        "[回到家乡](https://www.aila-town.com)\n\n"
        "门亮了。她一步跨过去，回到了链接镇。",
        hyperlinks=True,
    )

    tail_styles = [
        style
        for style, text in block.fragments
        if "门亮了" in text
    ]

    assert tail_styles == [""]
    assert terminal_hyperlink_from_style(tail_styles[0]) is None


@pytest.mark.anyio
async def test_dynamic_tui_hyperlink_cells_are_self_contained() -> None:
    stream = io.StringIO()
    output = Vt100_Output(
        stream,
        lambda: Size(rows=10, columns=20),
        term="xterm-256color",
        enable_cpr=False,
    )
    capabilities = TerminalCapabilities(
        TerminalIdentity(TerminalKind.ITERM2, "iTerm2"),
        TerminalColorSupport.fixed(TerminalColorLevel.TRUECOLOR),
    )

    with create_pipe_input() as pipe_input:
        runtime = TuiRuntime(
            input_obj=pipe_input,
            output_obj=output,
            terminal_capabilities=capabilities,
        )
        await runtime.open()
        try:
            assert runtime.hyperlinks_enabled
            await _render_next_frame(runtime)
            stream.seek(0)
            stream.truncate(0)

            runtime.set_active_renderable(
                render_tui_assistant_markdown(
                    "[abcdefghijklmnopqr](https://example.com/docs)"
                    "\n\nplain tail",
                    20,
                    hyperlinks=runtime.hyperlinks_enabled,
                ),
                kind="assistant",
            )
            await _render_next_frame(runtime)

            payload = stream.getvalue()
            open_sequence = "\x1b]8;;https://example.com/docs\x1b\\"

            assert open_sequence in payload
            assert payload.count(open_sequence) == payload.count(OSC8_CLOSE)
            assert "?]8;;" not in payload
            assert all(
                cell.char.count(open_sequence) == cell.char.count(OSC8_CLOSE)
                for row in runtime.screen.application.renderer
                .last_rendered_screen.data_buffer.values()
                for cell in row.values()
            )

            stream.seek(0)
            stream.truncate(0)
            runtime.set_active_renderable(
                render_tui_assistant_markdown(
                    "[abcdefghijklmnopqr](https://example.com/new)"
                    "\n\nplain tail",
                    20,
                    hyperlinks=runtime.hyperlinks_enabled,
                ),
                kind="assistant",
            )
            await _render_next_frame(runtime)

            update = stream.getvalue()
            updated_open = "\x1b]8;;https://example.com/new\x1b\\"

            assert updated_open in update
            assert update.count(updated_open) == update.count(OSC8_CLOSE)
        finally:
            await runtime.close()


@pytest.mark.anyio
async def test_unknown_terminal_degrades_hyperlink_to_styled_text() -> None:
    runtime = TuiRuntime()
    output = TuiOutputControl("", runtime=runtime, animate=False)

    await output.append_assistant_delta("[docs](https://example.com/docs)")
    await output.prepare_external_output()

    fragments = runtime.document.blocks[-1].display_block.fragments
    assert fragments_text(fragments) == "• docs"
    assert all(style != "[ZeroWidthEscape]" for style, _text in fragments)


def test_fragment_sanitizer_only_allows_safe_osc8_sequences() -> None:
    fragments = sanitize_formatted_text((
        ("[ZeroWidthEscape]", "\x1b]52;c;payload\x1b\\"),
        ("[ZeroWidthEscape]", "\x1b]8;;relative/path\x1b\\"),
        (
            "[ZeroWidthEscape]",
            "\x1b]8;;https://example.com/docs\x1b\\",
        ),
        ("class:link", "docs"),
        ("[ZeroWidthEscape]", "\x1b]8;;\x1b\\"),
    ))

    assert fragments_text(fragments) == "docs"
    assert fragments == [
        (
            "[ZeroWidthEscape]",
            "\x1b]8;;https://example.com/docs\x1b\\",
        ),
        ("class:link", "docs"),
        ("[ZeroWidthEscape]", "\x1b]8;;\x1b\\"),
    ]


def test_transcript_overlay_excludes_activity_animation() -> None:
    runtime = TuiRuntime()
    runtime.append_block(_block("stable"), kind="assistant")
    activity = _block("Thinking frame")
    runtime.screen.set_activity_renderable(activity)

    document_snapshot = runtime.document.transcript_snapshot()
    assert document_snapshot.live_tail is None

    runtime.toggle_transcript_overlay()
    overlay = runtime.screen.transcript_overlay
    assert "Thinking frame" not in "".join(
        text for _style, text in overlay.fragments()
    )
    assert overlay._cached_live_tail_key is None

    with patch.object(runtime.screen, "invalidate") as invalidate:
        runtime.screen.set_activity_renderable(_block("Thinking next frame"))

    invalidate.assert_not_called()
    assert "Thinking next frame" not in "".join(
        text for _style, text in overlay.fragments()
    )
    assert overlay._cached_live_tail_key is None

    runtime.screen.clear_activity_renderable()
    assert "Thinking next frame" not in "".join(
        text for _style, text in overlay.fragments()
    )
    assert overlay._cached_live_tail_key is None


@pytest.mark.anyio
async def test_transcript_overlay_styles_command_status_after_full_output() -> None:
    runtime = TuiRuntime()
    output = TuiOutputControl("", runtime=runtime, animate=False)
    presentation = TuiPresentationSink(output)

    await presentation.emit(build_native_tool_result_view(
        "shell_command",
        {"command": "echo ready"},
        ok=True,
        data={
            "command": "echo ready",
            "output_lines": ["ready"],
            "exit_code": 0,
        },
        cost_ms=0,
    ))

    assert "✓" not in _document_text(runtime.document)

    runtime.toggle_transcript_overlay()
    fragments = runtime.screen.transcript_overlay.fragments()
    text = "".join(value for _style, value in fragments)

    assert text.splitlines()[-2:] == ["ready", "✓ • 0ms"]
    assert ("class:terminal.success bold", "✓") in fragments
    assert ("dim", " • 0ms") in fragments


def test_transcript_overlay_stream_continuation_controls_shared_spacing() -> None:
    runtime = TuiRuntime()
    runtime.append_block(_block("first"), kind="assistant")
    runtime.set_active_renderable(
        _block("second"),
        kind="assistant",
        stream_continuation=False,
    )
    runtime.toggle_transcript_overlay()
    overlay = runtime.screen.transcript_overlay

    assert "".join(text for _style, text in overlay.fragments()) == (
        "first\n\nsecond"
    )
    first_key = overlay._cached_live_tail_key

    runtime.set_active_renderable(
        _block("second"),
        kind="assistant",
        stream_continuation=True,
    )
    transcript = "".join(text for _style, text in overlay.fragments())
    second_key = overlay._cached_live_tail_key

    assert transcript == "first\nsecond"
    assert _document_text(runtime.document) == transcript
    assert first_key is not None
    assert second_key is not None
    assert not first_key.stream_continuation
    assert second_key.stream_continuation


def test_transcript_overlay_reuses_existing_cells_when_stable_content_grows() -> None:
    runtime = TuiRuntime()
    runtime.append_block(_block("first"), kind="assistant")
    first_cell = runtime.document.blocks[0]
    overlay = runtime.screen.transcript_overlay

    with patch.object(
        runtime.document,
        "transcript_cell_fragments",
        wraps=runtime.document.transcript_cell_fragments,
    ) as render_cell:
        runtime.toggle_transcript_overlay()
        assert "first" in "".join(
            text for _style, text in overlay.fragments()
        )

        runtime.append_block(_block("second"), kind="assistant")
        second_cell = runtime.document.blocks[-1]
        assert "second" in "".join(
            text for _style, text in overlay.fragments()
        )

    first_renders = sum(
        call.args[0] is first_cell
        for call in render_cell.call_args_list
    )
    assert first_renders == 1
    assert sum(
        call.args[0] is second_cell
        for call in render_cell.call_args_list
    ) == 1
    assert overlay._cached_stable_cells == (first_cell, second_cell)


@pytest.mark.parametrize("restored_count", [3, 2, 1])
def test_transcript_overlay_discards_cache_when_transcript_is_replaced(
    restored_count: int,
) -> None:
    runtime = TuiRuntime()
    runtime.append_block(_block("old one"), kind="assistant")
    runtime.append_block(_block("old two"), kind="assistant")
    overlay = runtime.screen.transcript_overlay

    runtime.toggle_transcript_overlay()
    assert "".join(text for _style, text in overlay.fragments()) == (
        "old one\n\nold two"
    )
    old_cells = tuple(runtime.document.blocks)
    runtime.toggle_transcript_overlay()

    restored = tuple(
        TranscriptBlock(
            display_block=_block(f"restored {index}"),
            transcript_block=_block(f"restored {index}"),
            kind="assistant",
        )
        for index in range(restored_count)
    )
    runtime.replace_transcript(restored)
    runtime.toggle_transcript_overlay()

    transcript = "".join(text for _style, text in overlay.fragments())
    assert transcript == "\n\n".join(
        f"restored {index}" for index in range(restored_count)
    )
    assert all(id(cell) not in overlay._stable_cell_cache for cell in old_cells)


def test_transcript_overlay_renders_each_cell_once_during_long_session_growth() -> None:
    runtime = TuiRuntime()
    runtime.screen._output_size = lambda: (72, 18)
    runtime.append_block(_block("cell 0"), kind="assistant")
    overlay = runtime.screen.transcript_overlay

    with patch.object(
        runtime.document,
        "transcript_cell_fragments",
        wraps=runtime.document.transcript_cell_fragments,
    ) as render_cell:
        runtime.toggle_transcript_overlay()
        for index in range(1, 301):
            runtime.append_block(
                _block(f"cell {index}"),
                kind="assistant",
            )

        transcript = "".join(
            text for _style, text in overlay.fragments()
        )

    assert render_cell.call_count == 301
    assert len(overlay._stable_cell_cache) == 301
    assert transcript.startswith("cell 0\n\ncell 1")
    assert transcript.endswith("cell 300")


def test_transcript_overlay_bounds_cell_renders_without_losing_backtrack() -> None:
    runtime = TuiRuntime()
    output_size = [72, 18]
    runtime.screen._output_size = lambda: tuple(output_size)
    runtime.append_block(_block("first prompt"), kind="user")
    assert runtime.document.bind_latest_user_turn(
        "turn_first",
        "first prompt",
    )
    first_cell = runtime.document.blocks[0]
    overlay = runtime.screen.transcript_overlay

    with patch.object(
        runtime.document,
        "transcript_cell_fragments",
        wraps=runtime.document.transcript_cell_fragments,
    ) as render_cell:
        runtime.toggle_transcript_overlay()
        for index in range(1100):
            runtime.append_block(
                _block(f"cell {index}"),
                kind="assistant",
            )

    assert render_cell.call_count == 1101
    assert len(overlay._stable_cell_cache) == overlay.STABLE_CELL_CACHE_LIMIT
    assert id(first_cell) not in overlay._stable_cell_cache
    assert overlay._stable_cell_line_counts[id(first_cell)] == 1

    output_size[0] = 48
    overlay.fragments()

    assert len(overlay._stable_cell_cache) == overlay.STABLE_CELL_CACHE_LIMIT
    assert len(overlay._stable_cell_line_counts) == 1101
    assert overlay.begin_or_step_backtrack()
    assert overlay.scroll_offset == 0


def test_transcript_overlay_virtualizes_visual_lines_for_long_sessions() -> None:
    runtime = TuiRuntime()
    runtime.screen._output_size = lambda: (72, 12)
    for index in range(2500):
        runtime.append_block(
            _block(f"cell {index:04d}"),
            kind="assistant",
        )

    overlay = runtime.screen.transcript_overlay
    runtime.toggle_transcript_overlay()

    assert overlay._stable_line_count == 4999
    assert len(overlay._stable_cell_rows) == 2500
    assert len(overlay._stable_cell_cache) == overlay.STABLE_CELL_CACHE_LIMIT
    assert not hasattr(overlay, "_cached_stable_lines")

    overlay.jump_top()
    with patch.object(
        runtime.document,
        "transcript_cell_fragments",
        wraps=runtime.document.transcript_cell_fragments,
    ) as render_cell, patch.object(
        overlay,
        "_all_lines",
        side_effect=AssertionError("viewport materialized all transcript lines"),
    ):
        top = fragments_text(overlay.visible_fragments())
        top_renders = render_cell.call_count

        overlay.scroll_offset = overlay._stable_line_count // 2
        middle = fragments_text(overlay.visible_fragments())
        middle_renders = render_cell.call_count - top_renders

        overlay.jump_bottom()
        bottom = fragments_text(overlay.visible_fragments())
        bottom_renders = render_cell.call_count - top_renders - middle_renders

    assert "cell 0000" in top
    assert "cell 1250" in middle
    assert "cell 2499" in bottom
    assert 0 < top_renders <= 7
    assert 0 < middle_renders <= 7
    assert bottom_renders <= 7
    assert overlay.scroll_percentage() == 100


def test_transcript_overlay_index_matches_every_full_transcript_slice() -> None:
    runtime = TuiRuntime()
    runtime.screen._output_size = lambda: (12, 6)
    runtime.append_block(_block("alpha\nbeta"), kind="assistant")
    runtime.append_block(
        _block("continued " + "x" * 12),
        kind="assistant",
        stream_continuation=True,
    )
    runtime.append_block(_block("omega\nlast"), kind="operation")
    runtime.toggle_transcript_overlay()
    overlay = runtime.screen.transcript_overlay

    expected = split_formatted_lines(overlay.fragments())
    for start in range(len(expected) + 1):
        for count in (0, 1, 2, 5):
            assert overlay._visible_lines(start=start, count=count) == (
                expected[start:start + count]
            )


def test_transcript_overlay_survives_resize_and_continuous_active_output() -> None:
    runtime = TuiRuntime()
    output_size = [48, 12]
    runtime.screen._output_size = lambda: tuple(output_size)
    for index in range(120):
        runtime.append_block(
            _block(f"stable {index} " + "x" * 40),
            kind="operation",
        )

    overlay = runtime.screen.transcript_overlay
    runtime.toggle_transcript_overlay()
    overlay.jump_top()
    overlay.scroll_line(20)

    for width, height in ((32, 10), (96, 20), (41, 14), (72, 18)):
        output_size[:] = [width, height]
        overlay.visible_fragments()
        assert overlay._cached_width == width
        assert len(overlay._stable_cell_cache) == 120
        assert overlay.scroll_offset == 20

    stable_cells = tuple(runtime.document.blocks)
    with patch.object(
        runtime.document,
        "transcript_cell_fragments",
        wraps=runtime.document.transcript_cell_fragments,
    ) as render_cell, patch.object(
        overlay,
        "_all_lines",
        side_effect=AssertionError("viewport materialized all transcript lines"),
    ):
        for index in range(60):
            runtime.set_active_renderable(
                _block("running"),
                kind="operation",
                transcript_block=_block(
                    "$ command\n"
                    + "\n".join(
                        f"output {line}" for line in range(index + 1)
                    )
                ),
            )
            overlay.visible_fragments()

    stable_renders = sum(
        any(call.args[0] is cell for cell in stable_cells)
        for call in render_cell.call_args_list
    )
    assert stable_renders == 0
    assert overlay.scroll_offset == 20
    assert not overlay.follow_bottom
    assert "output 59" in "".join(
        text for _style, text in overlay.fragments()
    )

    overlay.jump_bottom()
    bottom_offset = overlay.scroll_offset
    runtime.set_active_renderable(
        _block("running"),
        kind="operation",
        transcript_block=_block("$ command\nfinal live line"),
    )

    assert overlay.follow_bottom
    assert overlay.scroll_offset <= bottom_offset
    assert overlay.scroll_percentage() == 100


def test_transcript_overlay_rebuilds_cells_after_resize_and_removal() -> None:
    runtime = TuiRuntime()
    output_size = [40, 10]
    runtime.screen._output_size = lambda: tuple(output_size)
    runtime.append_block(_block("first"), kind="assistant")
    runtime.append_block(_block("second"), kind="assistant")
    overlay = runtime.screen.transcript_overlay
    runtime.toggle_transcript_overlay()
    overlay.fragments()
    stable_cells = tuple(runtime.document.blocks)

    with patch.object(
        overlay,
        "_rebuild_stable_index",
        wraps=overlay._rebuild_stable_index,
    ) as rebuild_index:
        output_size[0] = 60
        overlay.fragments()

        assert stable_cells in [
            call.args[0] for call in rebuild_index.call_args_list
        ]

        rebuild_index.reset_mock()
        runtime.document.replace_blocks(stable_cells[:-1])
        overlay.content_changed()
        overlay.fragments()

    assert (stable_cells[0],) in [
        call.args[0] for call in rebuild_index.call_args_list
    ]
    assert id(stable_cells[-1]) not in overlay._stable_cell_cache


def test_transcript_overlay_reflows_live_tail_again_when_committed() -> None:
    runtime = TuiRuntime()
    output_size = [24, 10]
    runtime.screen._output_size = lambda: tuple(output_size)
    transcript_block = _block("$ command\n" + "output " * 12)
    runtime.set_active_renderable(
        _block("running"),
        kind="operation",
        transcript_block=transcript_block,
    )
    runtime.toggle_transcript_overlay()
    overlay = runtime.screen.transcript_overlay

    narrow_lines = split_formatted_lines(overlay.fragments())
    output_size[0] = 60
    wide_live = overlay.fragments()
    wide_lines = split_formatted_lines(wide_live)

    assert len(wide_lines) < len(narrow_lines)
    assert overlay._cached_width == 60
    assert overlay._cached_live_tail_key is not None

    runtime.commit_active_renderable(
        _block("completed"),
        transcript_block=transcript_block,
    )
    committed = overlay.fragments()

    assert committed == wide_live
    assert overlay._cached_width == 60
    assert overlay._cached_live_tail_key is None


def test_transcript_overlay_promotes_active_tail_without_duplicate_content() -> None:
    runtime = TuiRuntime()
    runtime.append_block(_block("first"), kind="assistant")
    runtime.set_active_renderable(
        _block("running"),
        kind="operation",
        transcript_block=_block("$ command\nlive output"),
    )
    runtime.toggle_transcript_overlay()
    overlay = runtime.screen.transcript_overlay
    before = "".join(text for _style, text in overlay.fragments())

    runtime.commit_active_renderable(
        _block("completed"),
        transcript_block=_block("$ command\nlive output"),
    )
    after = "".join(text for _style, text in overlay.fragments())

    assert before == after
    assert after.count("$ command") == 1
    snapshot = runtime.document.transcript_snapshot()
    assert len(snapshot.committed_cells) == 2
    assert snapshot.live_tail is None


def test_transcript_overlay_owns_visible_rows_and_fills_unused_space() -> None:
    runtime = TuiRuntime()
    runtime.screen._output_size = lambda: (40, 10)
    runtime.append_block(
        _block("compact"),
        kind="operation",
        transcript_block=_block("full output"),
    )

    runtime.toggle_transcript_overlay()

    visible = "".join(
        text
        for _style, text in runtime.screen.transcript_overlay.visible_fragments()
    )
    assert visible.splitlines() == ["full output", "~", "~", "~", "~"]
    assert runtime.screen.transcript_overlay.scroll_offset == 0
    assert runtime.screen.transcript_overlay.scroll_percentage() == 100


def test_transcript_overlay_wraps_cells_before_selecting_visible_rows() -> None:
    runtime = TuiRuntime()
    runtime.screen._output_size = lambda: (20, 10)
    assistant = FragmentBlock((
        (ASSISTANT_PREFIX_CLASS, "• "),
        ("", "x" * 25),
    ))
    runtime.append_block(assistant, kind="assistant")

    runtime.toggle_transcript_overlay()

    lines = split_formatted_lines(
        runtime.screen.transcript_overlay.fragments()
    )
    assert len(lines) == 2
    assert lines[1][0] == (ASSISTANT_PREFIX_CLASS, "  ")
    assert all(
        get_cwidth("".join(text for _style, text in line)) <= 20
        for line in lines
    )


@pytest.mark.parametrize(
    ("prefix_length", "unit"),
    [
        (19, "A\u0301"),
        (18, "👩\u200d💻"),
        (18, "🇨🇳"),
    ],
)
def test_transcript_overlay_does_not_split_combined_text_units(
    prefix_length: int,
    unit: str,
) -> None:
    runtime = TuiRuntime()
    runtime.screen._output_size = lambda: (20, 10)
    source = f"{'x' * prefix_length}{unit}tail"
    runtime.append_block(_block(source), kind="operation")
    runtime.toggle_transcript_overlay()

    lines = split_formatted_lines(runtime.screen.transcript_overlay.fragments())
    rendered_lines = [
        "".join(text for _style, text in line)
        for line in lines
    ]

    assert any(unit in line for line in rendered_lines)
    assert "".join(rendered_lines) == source


def test_transcript_overlay_keeps_text_unit_split_across_styles_together() -> None:
    runtime = TuiRuntime()
    runtime.screen._output_size = lambda: (20, 10)
    unit = "👩\u200d💻"
    source = f"{'x' * 18}{unit}tail"
    runtime.append_block(FragmentBlock((
        ("class:first", f"{'x' * 18}👩"),
        ("class:second", "\u200d💻tail"),
    )), kind="operation")
    runtime.toggle_transcript_overlay()

    lines = split_formatted_lines(runtime.screen.transcript_overlay.fragments())
    rendered_lines = [
        "".join(text for _style, text in line)
        for line in lines
    ]

    assert any(unit in line for line in rendered_lines)
    assert "".join(rendered_lines) == source


def test_transcript_overlay_rewraps_long_url_without_losing_text() -> None:
    runtime = TuiRuntime()
    output_size = [24, 10]
    runtime.screen._output_size = lambda: tuple(output_size)
    url = (
        "https://example.test/deep/path/to/resource?"
        "query=abcdefghijklmnopqrstuvwxyz&mode=transcript"
    )
    runtime.append_block(_block(url), kind="operation")
    runtime.toggle_transcript_overlay()
    overlay = runtime.screen.transcript_overlay

    narrow = split_formatted_lines(overlay.fragments())
    output_size[0] = 72
    wide = split_formatted_lines(overlay.fragments())

    assert len(wide) < len(narrow)
    assert "".join(
        text for line in narrow for _style, text in line
    ) == url
    assert "".join(
        text for line in wide for _style, text in line
    ) == url


@pytest.mark.anyio
async def test_submission_changes_preserve_open_transcript_reader_position() -> None:
    runtime = TuiRuntime()
    runtime.screen._output_size = lambda: (40, 10)
    runtime.append_block(
        _block("\n".join(f"line {index}" for index in range(20))),
        kind="assistant",
    )
    runtime.toggle_transcript_overlay()
    overlay = runtime.screen.transcript_overlay
    overlay.jump_top()

    runtime.submissions.enqueue_message("new question")
    with patch.object(
        overlay,
        "content_changed",
        wraps=overlay.content_changed,
    ) as content_changed:
        value = await runtime.read_message(PromptContext(
            model="test",
        ))

    assert value == "new question"
    assert content_changed.call_count == 1
    assert not overlay.follow_bottom
    assert overlay.scroll_offset == 0
    assert "new question" not in "".join(
        text for _style, text in overlay.visible_fragments()
    )
    assert "new question" in "".join(
        text for _style, text in overlay.fragments()
    )

    with patch.object(
        overlay,
        "content_changed",
        wraps=overlay.content_changed,
    ) as content_changed:
        runtime.discard_pending_submission()

    assert content_changed.call_count == 0
    assert "new question" in "".join(
        text for _style, text in overlay.fragments()
    )
    assert not overlay.follow_bottom
    assert overlay.scroll_offset == 0


@pytest.mark.anyio
async def test_transcript_overlay_defers_native_scrollback() -> None:
    with create_pipe_input() as pipe_input:
        runtime = TuiRuntime(input_obj=pipe_input, output_obj=DummyOutput())
        runtime.screen._output_size = lambda: (40, 8)
        await runtime.open()
        try:
            runtime.toggle_transcript_overlay()
            runtime.append_block(
                _block("\n".join(f"display {index}" for index in range(30))),
                kind="operation",
                transcript_block=_block(
                    "\n".join(f"transcript {index}" for index in range(30))
                ),
            )
            await asyncio.sleep(0)

            assert runtime.screen.transcript_overlay.active
            assert runtime.viewport.scrollback_task is None
            assert runtime.document.scrollback_line_count == 0
        finally:
            await runtime.close()
