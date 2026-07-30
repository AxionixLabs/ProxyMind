# -*- coding: utf-8 -*-

import asyncio
from types import SimpleNamespace
from typing import get_args
from unittest.mock import (
    AsyncMock,
    Mock,
    patch,
)

import pytest
from prompt_toolkit.data_structures import Size
from prompt_toolkit.input.defaults import create_pipe_input
from prompt_toolkit.output import DummyOutput
from prompt_toolkit.utils import get_cwidth

from mind_app.interaction.contracts import PromptContext
from mind_app.output.content import (
    AssistantTextDelta,
    SourcesOutput,
)
from mind_app.presentation.approval_views import build_approval_view
from mind_app.presentation.lifecycle_views import build_failure_view
from mind_app.presentation.models import (
    PlanItemView,
    PlanUpdateView,
)
from mind_app.presentation.tool_views import (
    build_generic_tool_result_view,
    build_native_tool_result_view,
    build_tool_start_view,
)
from mind_app.tui.adapters.content import TuiContentSink
from mind_app.tui.adapters.output import TuiOutputControl
from mind_app.tui.adapters.presentation import TuiPresentationSink
from mind_app.tui.core.assistant import TuiAssistantStream
from mind_app.tui.core.document import (
    TuiBlockKind,
    TuiDocument,
)
from mind_app.tui.core.models import FragmentBlock
from mind_app.tui.core.queued import TuiQueuedMessages, TuiSubmission
from mind_app.tui.core.render import (
    display_line_count,
    fragment_continuation_widths,
    join_formatted_lines,
    split_formatted_lines,
)
from mind_app.tui.core.runtime import TuiRuntime
from mind_app.tui.core.screen import _erase_terminal_scrollback
from mind_app.tui.core.styles import ASSISTANT_PREFIX_CLASS


def _block(text: str) -> FragmentBlock:
    return FragmentBlock((("", text),))


def _document_text(document: TuiDocument) -> str:
    return "".join(text for _style, text in document.fragments(width=80))


def test_formatted_line_split_round_trips_styles_and_blank_lines() -> None:
    fragments = [
        ("class:first", "one\n"),
        ("class:second", "\ntwo"),
    ]

    lines = split_formatted_lines(fragments)

    assert lines == [
        [("class:first", "one")],
        [],
        [("class:second", "two")],
    ]
    assert join_formatted_lines(lines) == [
        ("class:first", "one"),
        ("", "\n"),
        ("", "\n"),
        ("class:second", "two"),
    ]


@pytest.mark.parametrize("first_kind", get_args(TuiBlockKind))
@pytest.mark.parametrize("second_kind", get_args(TuiBlockKind))
def test_document_separates_every_block_transition(
    first_kind: TuiBlockKind,
    second_kind: TuiBlockKind,
) -> None:
    document = TuiDocument()

    document.append_block(_block("first"), kind=first_kind)
    document.append_block(_block("second"), kind=second_kind)

    assert _document_text(document) == "first\n\nsecond"


def test_block_outer_newlines_do_not_duplicate_document_spacing() -> None:
    document = TuiDocument()

    document.append_block(_block("\nfirst\n"), kind="operation")
    document.append_block(_block("\nsecond\n"), kind="operation")

    assert _document_text(document) == "first\n\nsecond"


def test_document_sanitizes_control_sequences_across_fragments() -> None:
    document = TuiDocument()
    block = FragmentBlock((
        ("class:first", "safe\x1b]52;c;"),
        ("class:second", "payload\x1b\\\tdevice\x1bPprivate\x1b\\"),
    ))

    document.append_block(block, kind="operation")

    stored = document.blocks[0].block
    text = "".join(value for _style, value in stored.fragments)
    assert text == "safe    device"
    assert "\x1b" not in text
    assert "payload" not in text
    assert "private" not in text


def test_document_preserves_safe_fragment_boundaries_and_identity() -> None:
    document = TuiDocument()
    block = FragmentBlock((
        ("class:notice", "•"),
        ("class:notice", " safe"),
    ))

    document.append_block(block, kind="notice")

    assert document.blocks[0].block is block
    assert document.blocks[0].block.fragments == block.fragments


def test_document_detects_only_user_or_assistant_conversation() -> None:
    document = TuiDocument()
    document.append_block(_block("intro"), kind="system")

    assert not document.has_conversation

    document.append_block(_block("question"), kind="user")

    assert document.has_conversation


def test_queued_messages_filter_controls_before_clipping() -> None:
    queued = TuiQueuedMessages()
    queued.append(TuiSubmission(
        value="raw",
        editable_text="id\tdevice\x1b]52;c;payload\x1b\\",
        paste_store={},
    ))

    fragments = queued.fragments(width=24)
    text = "".join(value for _style, value in fragments)

    assert "\x1b" not in text
    assert "payload" not in text
    assert all(
        get_cwidth(line) <= 24
        for line in text.splitlines()
    )


def test_footer_filters_context_controls_before_clipping() -> None:
    runtime = TuiRuntime()
    runtime.set_prompt_context(PromptContext(
        mode="chat",
        model="model\x1b]52;c;payload\x1b\\",
        workspace_label="workspace\x1bPprivate\x1b\\",
        permissions_label="Auto",
    ))

    text = "".join(
        value for _style, value in runtime.screen._footer_fragments()
    )

    assert "model" in text
    assert "workspace" in text
    assert "\x1b" not in text
    assert "payload" not in text
    assert "private" not in text


def test_active_block_keeps_spacing_while_it_is_updated_and_committed() -> None:
    document = TuiDocument()
    document.append_block(_block("tool"), kind="operation")

    document.set_active(_block("draft"), kind="assistant")
    document.set_active(_block("final"), kind="assistant")
    document.commit_active(_block("final"))

    assert _document_text(document) == "tool\n\nfinal"
    assert document.blocks[-1].kind == "assistant"


def test_running_command_error_enters_document_after_active_block() -> None:
    runtime = TuiRuntime()
    runtime.append_block(_block("approved"), kind="approval")
    runtime.set_active_renderable(_block("streaming"), kind="assistant")
    runtime.execution_active = True
    runtime.screen.input.buffer.text = "/new"

    runtime.submissions.accept_input(runtime.screen.input.buffer)

    assert _document_text(runtime.document) == (
        "approved\n\nstreaming\n\n"
        "■ '/new' is disabled while a task is in progress."
    )
    assert runtime.screen._queued_fragments() == []

    runtime.commit_active_renderable(_block("completed"))

    assert [item.kind for item in runtime.document.blocks] == [
        "approval",
        "assistant",
        "notice",
    ]
    assert _document_text(runtime.document) == (
        "approved\n\ncompleted\n\n"
        "■ '/new' is disabled while a task is in progress."
    )


def test_scrollback_and_clear_boundaries_keep_complete_archive() -> None:
    document = TuiDocument()
    document.append_block(_block("first"), kind="assistant")
    document.append_block(_block("second"), kind="operation")

    assert "".join(
        text for _style, text in document.scrollback_prefix_fragments(2)
    ) == "first\n"

    document.commit_scrollback_prefix(2)

    assert len(document.blocks) == 2
    assert document.scrollback_line_count == 2
    assert document.cleared_line_count == 0
    assert "first" not in _document_text(document)
    assert "second" in _document_text(document)

    document.clear_visible_prefix()

    assert document.scrollback_line_count == 2
    assert document.cleared_line_count == 3
    assert _document_text(document) == ""

    document.append_block(_block("third"), kind="assistant")

    assert _document_text(document) == "third"
    assert "".join(
        text
        for _style, text in document.scrollback_prefix_fragments(1)
    ) == "third"

    document.commit_scrollback_prefix(1)

    assert document.scrollback_line_count == 5
    assert "".join(
        text for _style, text in document.all_fragments(width=80)
    ) == "first\n\nsecond\n\nthird"


def test_ctrl_l_clear_is_repeatable_and_keeps_active_block() -> None:
    runtime = TuiRuntime()
    runtime.append_block(_block("old history"), kind="operation")
    runtime.set_active_renderable(_block("streaming"), kind="assistant")

    with patch.object(runtime.screen.application.renderer, "clear") as clear:
        runtime.viewport.clear_visible()

        assert _document_text(runtime.document) == "streaming"
        assert runtime.document.scrollback_line_count == 0
        assert runtime.document.cleared_line_count == 1

        runtime.commit_active_renderable(_block("completed"))
        runtime.append_block(_block("new result"), kind="operation")

        assert _document_text(runtime.document) == "completed\n\nnew result"

        runtime.viewport.clear_visible()

        assert _document_text(runtime.document) == ""
        assert runtime.document.scrollback_line_count == 0
        assert runtime.document.cleared_line_count == 5
        assert clear.call_count == 2

    runtime.append_block(_block("after clear"), kind="assistant")

    assert _document_text(runtime.document) == "after clear"
    assert "".join(
        text for _style, text in runtime.document.all_fragments(width=80)
    ) == "old history\n\ncompleted\n\nnew result\n\nafter clear"


def test_ctrl_l_cancels_pending_scrollback_before_clearing() -> None:
    runtime = TuiRuntime()
    scrollback_task = Mock()
    scrollback_task.done.return_value = False
    runtime.viewport._scrollback_task = scrollback_task

    with (
        patch.object(runtime.screen.application.renderer, "clear") as clear,
        patch(
            "mind_app.tui.core.screen._erase_terminal_scrollback"
        ) as erase_scrollback,
    ):
        runtime.viewport.clear_visible()

    scrollback_task.cancel.assert_called_once_with()
    clear.assert_called_once_with()
    erase_scrollback.assert_called_once_with(runtime.screen.application.output)


def test_terminal_scrollback_uses_vt_erase_sequence() -> None:
    output = SimpleNamespace(
        vt100_output=object(),
        write_raw=Mock(),
        flush=Mock(),
    )

    _erase_terminal_scrollback(output)

    output.write_raw.assert_called_once_with("\x1b[3J")
    output.flush.assert_called_once_with()


def test_terminal_scrollback_skips_raw_ansi_on_legacy_win32(
    monkeypatch,
) -> None:
    output = SimpleNamespace(
        write_raw=Mock(),
        flush=Mock(),
    )
    monkeypatch.setattr("mind_app.tui.core.screen.sys.platform", "win32")

    _erase_terminal_scrollback(output)

    output.write_raw.assert_not_called()
    output.flush.assert_not_called()


@pytest.mark.anyio
@pytest.mark.parametrize(
    "state_setter",
    ["set_execution_active", "set_foreground_active"],
)
async def test_busy_state_defers_scrollback_until_idle(state_setter) -> None:
    with create_pipe_input() as pipe_input:
        runtime = TuiRuntime(input_obj=pipe_input, output_obj=DummyOutput())

        with patch.object(
            runtime.screen.application.output,
            "get_size",
            return_value=Size(rows=10, columns=40),
        ):
            await runtime.open()
            try:
                with patch.object(
                    runtime.screen.application,
                    "print_text",
                    wraps=runtime.screen.application.print_text,
                ) as print_text:
                    set_busy = getattr(runtime, state_setter)
                    set_busy(True)
                    for index in range(6):
                        runtime.append_block(
                            _block(f"block {index}\n" + "line\n" * 3),
                            kind="operation",
                        )

                    await asyncio.sleep(0.02)
                    assert runtime.document.scrollback_line_count == 0
                    assert not print_text.called

                    set_busy(False)
                    await asyncio.sleep(0.02)

                assert len(runtime.document.blocks) == 6
                assert runtime.document.scrollback_line_count > 0
                assert print_text.called
                printed = "".join(
                    text
                    for call in print_text.call_args_list
                    for _style, text in call.args[0]
                )
                assert "block 0" in printed
                assert "block 0" in "".join(
                    text
                    for _style, text in runtime.document.all_fragments(width=40)
                )
            finally:
                await runtime.close()


@pytest.mark.anyio
async def test_scrollback_keeps_latest_oversized_reply_across_turns_and_resize() -> None:
    with create_pipe_input() as pipe_input:
        runtime = TuiRuntime(input_obj=pipe_input, output_obj=DummyOutput())
        terminal_size = Size(rows=30, columns=40)

        with patch.object(
            runtime.screen.application.output,
            "get_size",
            side_effect=lambda: terminal_size,
        ):
            await runtime.open()
            try:
                runtime.set_execution_active(True)
                runtime.append_block(_block("approved"), kind="approval")
                runtime.append_block(_block("tool result"), kind="operation")
                runtime.append_block(
                    _block("\n".join(f"first {index}" for index in range(30))),
                    kind="assistant",
                )
                runtime.append_block(_block("Finished first"), kind="system")

                terminal_size = Size(rows=10, columns=40)
                runtime.set_execution_active(False)
                await asyncio.sleep(0.02)

                assert runtime.document.scrollback_line_count > 2
                assert "first 29" in _document_text(runtime.document)

                runtime.set_execution_active(True)
                runtime.append_block(_block("next question"), kind="user")
                runtime.append_block(
                    _block("\n".join(f"second {index}" for index in range(30))),
                    kind="assistant",
                )
                runtime.append_block(_block("Finished second"), kind="system")
                runtime.set_execution_active(False)
                await asyncio.sleep(0.02)

                assert runtime.document.scrollback_line_count > 5
                visible = _document_text(runtime.document)
                assert "first 29" not in visible
                assert "second 29" in visible
            finally:
                await runtime.close()


@pytest.mark.anyio
async def test_single_oversized_block_retires_complete_logical_lines() -> None:
    with create_pipe_input() as pipe_input:
        runtime = TuiRuntime(input_obj=pipe_input, output_obj=DummyOutput())
        source = "\n".join(f"line {index:02d}" for index in range(80))
        expected = "\n".join([
            "• line 00",
            *(f"  line {index:02d}" for index in range(1, 80)),
        ])
        output = TuiOutputControl("", runtime=runtime, animate=False)

        with patch.object(
            runtime.screen.application.output,
            "get_size",
            return_value=Size(rows=12, columns=40),
        ):
            await runtime.open()
            try:
                with patch.object(
                    runtime.screen.application,
                    "print_text",
                    wraps=runtime.screen.application.print_text,
                ) as print_text:
                    runtime.set_execution_active(True)
                    await output.append_assistant_delta(source)
                    await output.settle_stream()
                    await output.prepare_external_output()

                    assert not print_text.called

                    runtime.set_execution_active(False)
                    await asyncio.sleep(0.02)

                assert print_text.call_count == 1

                printed = "".join(
                    text for _style, text in print_text.call_args.args[0]
                )
                visible = _document_text(runtime.document)

                assert printed.startswith("• line 00\n")
                assert not printed.endswith("\n")
                assert visible.endswith("line 79")
                assert f"{printed}\n{visible}" == expected
                assert runtime.document.scrollback_line_count > 0
                assert display_line_count(
                    visible,
                    width=runtime.terminal_width,
                ) <= runtime.screen.transcript_available_height()
            finally:
                await runtime.close()


@pytest.mark.anyio
async def test_line_scrollback_resize_never_reprints_retired_content() -> None:
    with create_pipe_input() as pipe_input:
        runtime = TuiRuntime(input_obj=pipe_input, output_obj=DummyOutput())
        terminal_size = Size(rows=12, columns=40)
        source = "\n".join(f"entry {index:02d}" for index in range(60))

        with patch.object(
            runtime.screen.application.output,
            "get_size",
            side_effect=lambda: terminal_size,
        ):
            await runtime.open()
            try:
                with patch.object(
                    runtime.screen.application,
                    "print_text",
                    wraps=runtime.screen.application.print_text,
                ) as print_text:
                    runtime.append_block(_block(source), kind="assistant")
                    await asyncio.sleep(0.02)
                    first_count = runtime.document.scrollback_line_count

                    terminal_size = Size(rows=20, columns=40)
                    runtime.viewport.schedule_scrollback_flush()
                    await asyncio.sleep(0.02)
                    assert runtime.document.scrollback_line_count == first_count

                    terminal_size = Size(rows=8, columns=40)
                    runtime.viewport.schedule_scrollback_flush()
                    await asyncio.sleep(0.02)

                chunks = [
                    "".join(text for _style, text in call.args[0])
                    for call in print_text.call_args_list
                ]
                visible = _document_text(runtime.document)

                assert len(chunks) == 2
                assert "\n".join([*chunks, visible]) == source
                assert runtime.document.scrollback_line_count > first_count
                assert "".join(
                    text
                    for _style, text in runtime.document.all_fragments(width=40)
                ) == source
            finally:
                await runtime.close()


@pytest.mark.anyio
async def test_queued_scrollback_rechecks_busy_state_before_flushing() -> None:
    with create_pipe_input() as pipe_input:
        runtime = TuiRuntime(input_obj=pipe_input, output_obj=DummyOutput())

        with patch.object(
            runtime.screen.application.output,
            "get_size",
            return_value=Size(rows=10, columns=40),
        ):
            await runtime.open()
            try:
                runtime.set_execution_active(True)
                for index in range(6):
                    runtime.append_block(
                        _block(f"block {index}\n" + "line\n" * 3),
                        kind="operation",
                    )

                runtime.set_execution_active(False)
                assert runtime.viewport.scrollback_task is not None
                runtime.set_execution_active(True)

                await asyncio.sleep(0.02)

                assert runtime.document.scrollback_line_count == 0

                runtime.set_execution_active(False)
                await asyncio.sleep(0.02)

                assert runtime.document.scrollback_line_count > 0
            finally:
                await runtime.close()


@pytest.mark.anyio
async def test_inline_canvas_grows_until_bottom_pane_reaches_terminal_edge() -> None:
    with create_pipe_input() as pipe_input:
        runtime = TuiRuntime(input_obj=pipe_input, output_obj=DummyOutput())

        with patch.object(
            runtime.screen.application.output,
            "get_size",
            return_value=Size(rows=12, columns=40),
        ):
            await runtime.open()
            try:
                assert not runtime.screen.application.full_screen
                initial_height = runtime.screen.canvas.preferred_height(40, 12)
                assert initial_height.min == 4
                assert initial_height.preferred == 4
                assert initial_height.max == 4

                initial_screen = runtime.screen.application.renderer.last_rendered_screen
                initial_top = initial_screen.visible_windows_to_write_positions[
                    runtime.screen.input_top_padding
                ]
                initial_input = initial_screen.visible_windows_to_write_positions[
                    runtime.screen.input.window
                ]
                initial_bottom = initial_screen.visible_windows_to_write_positions[
                    runtime.screen.input_bottom_padding
                ]
                initial_footer = initial_screen.visible_windows_to_write_positions[
                    runtime.screen.footer_window
                ]
                render_count = runtime.screen.application.render_counter
                runtime.screen.set_activity_renderable(_block("Thinking"))
                for _ in range(20):
                    await asyncio.sleep(0)
                    if runtime.screen.application.render_counter > render_count:
                        break

                active_screen = runtime.screen.application.renderer.last_rendered_screen
                active_input = active_screen.visible_windows_to_write_positions[
                    runtime.screen.input.window
                ]
                active_footer = active_screen.visible_windows_to_write_positions[
                    runtime.screen.footer_window
                ]
                assert initial_top.ypos == 0
                assert initial_input.ypos == 1
                assert initial_bottom.ypos == 2
                assert initial_footer.ypos == 3
                assert active_input.ypos > initial_input.ypos
                assert active_footer.ypos > initial_footer.ypos

                render_count = runtime.screen.application.render_counter
                runtime.append_block(_block("answer"), kind="assistant")
                for _ in range(20):
                    await asyncio.sleep(0)
                    if runtime.screen.application.render_counter > render_count:
                        break

                content_screen = runtime.screen.application.renderer.last_rendered_screen
                content_input = content_screen.visible_windows_to_write_positions[
                    runtime.screen.input.window
                ]
                content_footer = content_screen.visible_windows_to_write_positions[
                    runtime.screen.footer_window
                ]

                assert content_input.ypos > active_input.ypos
                assert content_footer.ypos > active_footer.ypos

                runtime.append_block(
                    _block("line\n" * 20),
                    kind="assistant",
                )
                await asyncio.sleep(0.02)
                committed_count = runtime.document.scrollback_line_count
                assert committed_count > 0
                assert len(runtime.document.blocks) == 2
                assert "line" in "".join(
                    text
                    for _style, text in runtime.document.all_fragments(width=40)
                )

                render_count = runtime.screen.application.render_counter
                runtime.append_block(_block("more\n" * 20), kind="operation")
                for _ in range(20):
                    await asyncio.sleep(0)
                    if runtime.screen.application.render_counter > render_count:
                        break

                assert runtime.document.scrollback_line_count > committed_count
                assert len(runtime.document.blocks) == 3
            finally:
                await runtime.close()


@pytest.mark.anyio
async def test_three_row_terminal_prioritizes_complete_input_surface() -> None:
    with create_pipe_input() as pipe_input:
        runtime = TuiRuntime(input_obj=pipe_input, output_obj=DummyOutput())

        with patch.object(
            runtime.screen.application.output,
            "get_size",
            return_value=Size(rows=3, columns=40),
        ):
            await runtime.open()
            try:
                screen = runtime.screen.application.renderer.last_rendered_screen
                positions = screen.visible_windows_to_write_positions

                assert positions[runtime.screen.input_top_padding].ypos == 0
                assert positions[runtime.screen.input.window].ypos == 1
                assert positions[runtime.screen.input_bottom_padding].ypos == 2
                assert runtime.screen.footer_window not in positions
                assert not runtime.screen._footer_visible()
            finally:
                await runtime.close()


@pytest.mark.anyio
async def test_multiline_input_grows_for_trailing_edit_line() -> None:
    with create_pipe_input() as pipe_input:
        runtime = TuiRuntime(input_obj=pipe_input, output_obj=DummyOutput())

        with patch.object(
            runtime.screen.application.output,
            "get_size",
            return_value=Size(rows=12, columns=40),
        ):
            await runtime.open()
            try:
                render_count = runtime.screen.application.render_counter
                runtime.screen.input.buffer.text = "first\nsecond\n"
                runtime.screen.input.buffer.cursor_position = len(
                    runtime.screen.input.buffer.text
                )
                for _ in range(20):
                    await asyncio.sleep(0)
                    if runtime.screen.application.render_counter > render_count:
                        break

                screen = runtime.screen.application.renderer.last_rendered_screen
                input_position = screen.visible_windows_to_write_positions[
                    runtime.screen.input.window
                ]

                assert runtime.screen._input_height() == 3
                assert runtime.screen._input_surface_height() == 5
                assert input_position.height == 3
            finally:
                await runtime.close()


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
                    if not runtime.document.has_visible_content:
                        break

                assert not runtime.document.has_visible_content
                assert runtime.document.cleared_line_count == 1
                assert runtime.screen.input.buffer.text == "draft input"

                runtime.append_block(_block("new answer"), kind="assistant")
                assert runtime.document.has_visible_content

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
async def test_presentation_separates_consecutive_tool_groups() -> None:
    runtime = TuiRuntime()
    output = TuiOutputControl("", runtime=runtime, animate=False)
    presentation = TuiPresentationSink(output)

    await output.append_assistant_delta("model output")
    await output.prepare_external_output()
    await presentation.emit(build_tool_start_view(
        "shell_command",
        {"command": "echo one"},
        call_id="one",
    ))
    await presentation.emit(build_tool_start_view(
        "shell_command",
        {"command": "echo two"},
        call_id="two",
    ))

    assert [item.kind for item in runtime.document.blocks] == [
        "assistant",
        "operation",
        "operation",
    ]
    assert [item.gap_before for item in runtime.document.blocks] == [
        False,
        True,
        True,
    ]
    assert runtime.document.active_block is None


@pytest.mark.anyio
async def test_generic_tool_result_starts_on_separate_visual_group() -> None:
    runtime = TuiRuntime()
    output = TuiOutputControl("", runtime=runtime, animate=False)
    presentation = TuiPresentationSink(output)

    await presentation.emit(build_tool_start_view(
        "remote_tool",
        {"query": "one"},
        call_id="one",
    ))
    await presentation.emit(build_generic_tool_result_view(
        "remote_tool",
        "result",
        ok=True,
        call_id="one",
    ))
    await presentation.emit(build_tool_start_view(
        "remote_tool",
        {"query": "two"},
        call_id="two",
    ))

    assert [item.gap_before for item in runtime.document.blocks] == [
        False,
        True,
        True,
    ]
    assert _document_text(runtime.document).count("\n\n") == 2


@pytest.mark.anyio
async def test_plan_update_is_separated_from_preceding_tool() -> None:
    runtime = TuiRuntime()
    output = TuiOutputControl("", runtime=runtime, animate=False)
    presentation = TuiPresentationSink(output)

    await presentation.emit(build_native_tool_result_view(
        "shell_command",
        {"command": "git status --short"},
        ok=True,
        data={"command": "git status --short", "output_lines": ["M file.py"]},
        call_id="status",
    ))
    await presentation.emit(PlanUpdateView(
        explanation="",
        items=(PlanItemView(step="检查结果", status="completed"),),
    ))

    assert [item.kind for item in runtime.document.blocks] == [
        "operation",
        "plan",
    ]
    assert [item.gap_before for item in runtime.document.blocks] == [
        False,
        True,
    ]
    assert "\n\n• Updated Plan" in _document_text(runtime.document)


@pytest.mark.anyio
async def test_native_tool_results_start_separate_tool_groups() -> None:
    runtime = TuiRuntime()
    output = TuiOutputControl("", runtime=runtime, animate=False)
    presentation = TuiPresentationSink(output)

    for call_id, command in (("one", "echo one"), ("two", "echo two")):
        await presentation.emit(build_native_tool_result_view(
            "shell_command",
            {"command": command},
            ok=True,
            data={"command": command, "output_lines": [call_id]},
            call_id=call_id,
        ))

    assert [item.gap_before for item in runtime.document.blocks] == [
        False,
        True,
    ]
    assert _document_text(runtime.document).count("\n\n") == 1


@pytest.mark.anyio
async def test_native_shell_result_strips_ansi_from_ordered_output() -> None:
    runtime = TuiRuntime()
    output = TuiOutputControl("", runtime=runtime, animate=False)
    presentation = TuiPresentationSink(output)

    await presentation.emit(build_native_tool_result_view(
        "shell_command",
        {"command": "Get-Item video.mp4 | Format-List"},
        ok=True,
        data={
            "command": "Get-Item video.mp4 | Format-List",
            "output_lines": ["\x1b[32;1mFullName : \x1b[0mvideo.mp4"],
        },
        call_id="ansi-output",
    ))

    document_text = _document_text(runtime.document)
    assert "\x1b" not in document_text
    assert "FullName : video.mp4" in document_text


@pytest.mark.anyio
async def test_approval_sequence_separates_resumed_operation() -> None:
    runtime = TuiRuntime()
    output = TuiOutputControl("", runtime=runtime, animate=False)
    presentation = TuiPresentationSink(output)
    approval = {
        "tool": "shell_command",
        "arguments": {"command": "echo approved"},
    }

    await presentation.emit(build_approval_view(approval, decision="accept"))
    await presentation.emit(build_approval_view(approval, decision="acceptForSession"))
    await presentation.emit(build_tool_start_view(
        "shell_command",
        approval["arguments"],
        call_id="approved",
    ))

    assert [item.kind for item in runtime.document.blocks] == [
        "approval",
        "approval",
        "operation",
    ]
    assert [item.gap_before for item in runtime.document.blocks] == [
        False,
        True,
        True,
    ]
    assert _document_text(runtime.document).count("\n\n") == 2


@pytest.mark.anyio
async def test_tool_approval_tool_sequence_separates_human_boundary() -> None:
    runtime = TuiRuntime()
    output = TuiOutputControl("", runtime=runtime, animate=False)
    presentation = TuiPresentationSink(output)
    approval = {
        "tool": "shell_command",
        "arguments": {"command": "echo approved"},
    }

    await presentation.emit(build_tool_start_view(
        "shell_command",
        {"command": "echo before"},
        call_id="before",
    ))
    await presentation.emit(build_approval_view(approval, decision="accept"))
    await presentation.emit(build_tool_start_view(
        "shell_command",
        {"command": "echo after"},
        call_id="after",
    ))

    assert [item.kind for item in runtime.document.blocks] == [
        "operation",
        "approval",
        "operation",
    ]
    assert [item.gap_before for item in runtime.document.blocks] == [
        False,
        True,
        True,
    ]


@pytest.mark.anyio
async def test_tui_stream_keeps_long_live_line_without_rich_clipping() -> None:
    runtime = TuiRuntime()
    output = TuiOutputControl("", runtime=runtime, animate=False)
    source = "x" * 200

    await output.append_assistant_delta(source)

    assert runtime.document.active_block is not None
    active_text = "".join(
        text for _style, text in runtime.document.active_block.fragments
    )
    assert active_text == f"• {source}"
    assert " ..." not in active_text

    await output.prepare_external_output()

    assert _document_text(runtime.document) == f"• {source}"


@pytest.mark.anyio
async def test_wide_character_stream_reflows_without_losing_content() -> None:
    runtime = TuiRuntime()
    output = TuiOutputControl("", runtime=runtime, animate=False)
    source = "中文" * 60

    await output.append_assistant_delta(source)

    visible = _document_text(runtime.document)
    assert visible == f"• {source}"
    assert display_line_count(visible, width=40) > display_line_count(
        visible,
        width=80,
    )


def test_stream_reveal_keeps_combined_text_units_intact() -> None:
    stream = TuiAssistantStream()
    stream.append("A\u0301👩\u200d💻🇨🇳x")

    stream.reveal(1)
    assert stream.visible_text == "A\u0301"

    stream.reveal(1)
    assert stream.visible_text == "A\u0301👩\u200d💻"

    stream.reveal(1)
    assert stream.visible_text == "A\u0301👩\u200d💻🇨🇳"


def test_assistant_wrap_prefix_is_included_in_display_rows() -> None:
    fragments = [(ASSISTANT_PREFIX_CLASS, f"• {'x' * 17}")]
    text = "".join(value for _style, value in fragments)
    continuation_widths = fragment_continuation_widths(
        fragments,
        prefix_style=ASSISTANT_PREFIX_CLASS,
        prefix_width=2,
    )

    assert display_line_count(text, width=10) == 2
    assert display_line_count(
        text,
        width=10,
        continuation_widths=continuation_widths,
    ) == 3


def test_transcript_height_uses_assistant_wrap_prefix() -> None:
    runtime = TuiRuntime()
    runtime.set_active_renderable(
        FragmentBlock(((ASSISTANT_PREFIX_CLASS, f"• {'x' * 37}"),)),
        kind="assistant",
    )

    with patch.object(
        runtime.screen.application.output,
        "get_size",
        return_value=Size(rows=24, columns=20),
    ):
        assert runtime.screen._transcript_dimension().preferred == 3


@pytest.mark.anyio
async def test_text_done_boundary_adds_one_assistant_continuation_line() -> None:
    runtime = TuiRuntime()
    output = TuiOutputControl("", runtime=runtime, animate=False)

    await output.append_assistant_delta("first")
    output.mark_stream_boundary()
    await output.append_assistant_delta("second")

    assert _document_text(runtime.document) == "• first\n  second"


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("first", "second"),
    [
        ("first\n", "second"),
        ("first", "\nsecond"),
    ],
)
async def test_text_done_boundary_does_not_duplicate_existing_newline(
    first: str,
    second: str,
) -> None:
    runtime = TuiRuntime()
    output = TuiOutputControl("", runtime=runtime, animate=False)

    await output.append_assistant_delta(first)
    output.mark_stream_boundary()
    await output.append_assistant_delta(second)

    assert _document_text(runtime.document) == "• first\n  second"


@pytest.mark.anyio
async def test_animated_stream_uses_the_same_text_done_boundary() -> None:
    runtime = TuiRuntime()
    output = TuiOutputControl("", runtime=runtime, animate=True)

    await output.append_assistant_delta("first")
    output.mark_stream_boundary()
    await output.append_assistant_delta("second")

    await output.settle_stream()

    assert _document_text(runtime.document) == "• first\n  second"


@pytest.mark.anyio
async def test_animated_stream_batches_rendering_to_frame_budget() -> None:
    runtime = TuiRuntime()
    output = TuiOutputControl("", runtime=runtime, animate=True)

    with patch.object(
        runtime,
        "set_active_renderable",
        wraps=runtime.set_active_renderable,
    ) as render:
        await output.append_assistant_delta("first")
        await output.append_assistant_delta(" second")
        await output.append_assistant_delta(" third")

        assert output.assistant.text == "first second third"
        assert output.assistant.visible_text != output.assistant.text
        assert render.call_count == 1

        await output.settle_stream()
        assert render.call_count == 2
        await asyncio.sleep(0.04)
        assert render.call_count == 2

    assert _document_text(runtime.document) == "• first second third"


@pytest.mark.anyio
async def test_animated_stream_drains_backlog_without_more_deltas() -> None:
    runtime = TuiRuntime()
    output = TuiOutputControl("", runtime=runtime, animate=True)
    source = "中文" * 50

    await output.append_assistant_delta(source)

    assert output.assistant.text == source
    assert 0 < output.assistant.pending_width <= 16

    for _ in range(50):
        if output.assistant.pending_length == 0:
            break
        await asyncio.sleep(0.01)

    assert output.assistant.visible_text == source
    assert output._stream_render_handle is None

    await output.settle_stream()
    assert _document_text(runtime.document) == f"• {source}"


@pytest.mark.anyio
async def test_animated_stream_cursor_retires_after_backlog_drains() -> None:
    runtime = TuiRuntime()
    output = TuiOutputControl("", runtime=runtime, animate=True)
    output._cursor = "█"

    await output.append_assistant_delta("done")

    assert output.assistant.pending_length == 0
    assert _document_text(runtime.document) == "• done█"
    assert output._stream_cursor_retire_handle is not None

    await asyncio.sleep(0.12)

    assert _document_text(runtime.document) == "• done"
    assert output._stream_cursor_retire_handle is None


@pytest.mark.anyio
async def test_new_delta_cancels_stream_cursor_retirement() -> None:
    runtime = TuiRuntime()
    output = TuiOutputControl("", runtime=runtime, animate=True)

    await output.append_assistant_delta("done")
    retire_handle = output._stream_cursor_retire_handle

    assert retire_handle is not None

    await output.append_assistant_delta(" next")

    assert retire_handle.cancelled()
    assert output._stream_cursor_retire_handle is None

    await output.settle_stream()
    assert _document_text(runtime.document) == "• done next"


def test_stream_render_budget_adapts_to_size_and_render_cost() -> None:
    output = TuiOutputControl("", runtime=TuiRuntime(), animate=True)

    output.assistant.text = "short"
    assert output._stream_render_interval() == 1 / 20

    output._stream_render_cost_sec = 1 / 80
    assert output._stream_render_interval() == 1 / 12

    output._stream_render_cost_sec = 0.0
    output.assistant.text = "x" * 2000
    assert output._stream_render_interval() == 1 / 12


def test_typewriter_cursor_does_not_create_a_transient_display_row() -> None:
    runtime = TuiRuntime()
    output = TuiOutputControl("", runtime=runtime, animate=True)
    output._cursor = "█"

    with patch.object(
        runtime.screen.application.output,
        "get_size",
        return_value=Size(rows=24, columns=40),
    ):
        output.assistant.append("short")
        output.assistant.reveal_all()
        output._render_active(cursor=True)
        assert _document_text(runtime.document).endswith("█")

        output.assistant.clear()
        output.assistant.append("x" * 38)
        output.assistant.reveal_all()
        output._render_active(cursor=True)
        active_text = _document_text(runtime.document)
        active_rows = display_line_count(active_text, width=40)
        output._render_active(cursor=False)

        assert active_text == f"• {'x' * 38}"
        assert display_line_count(
            _document_text(runtime.document),
            width=40,
        ) == active_rows


@pytest.mark.anyio
async def test_model_tool_model_sequence_has_one_blank_row_at_each_boundary() -> None:
    runtime = TuiRuntime()
    output = TuiOutputControl("", runtime=runtime, animate=False)
    presentation = TuiPresentationSink(output)

    await output.append_assistant_delta("before")
    await output.prepare_external_output()
    await presentation.emit(build_tool_start_view(
        "shell_command",
        {"command": "echo tool"},
        call_id="tool",
    ))
    await output.append_assistant_delta("after")
    await output.prepare_external_output()

    assert [item.kind for item in runtime.document.blocks] == [
        "assistant",
        "operation",
        "assistant",
    ]
    assert [item.gap_before for item in runtime.document.blocks] == [
        False,
        True,
        True,
    ]
    rendered = _document_text(runtime.document)
    assert rendered.startswith("• before\n\n")
    assert rendered.endswith("\n\n• after")
    assert rendered.count("\n\n") == 2


@pytest.mark.anyio
async def test_assistant_commit_renders_markdown_without_final_units_bridge() -> None:
    runtime = TuiRuntime()
    output = TuiOutputControl("", runtime=runtime, animate=False)

    await output.append_assistant_delta("**bold** and `code`")
    await output.prepare_external_output()

    fragments = runtime.document.blocks[-1].block.fragments
    assert "".join(text for _style, text in fragments) == "• bold and code"
    assert any("bold" in style and text == "bold" for style, text in fragments)
    assert any("fg:" in style and text == "code" for style, text in fragments)


@pytest.mark.anyio
async def test_settled_markdown_frame_is_reused_when_committed() -> None:
    runtime = TuiRuntime()
    output = TuiOutputControl("", runtime=runtime, animate=False)

    await output.append_assistant_delta("**bold** and `code`")
    await output.settle_stream()

    active = runtime.document.active_block
    assert active is not None
    assert "".join(text for _style, text in active.fragments) == "• bold and code"

    await output.prepare_external_output()

    assert runtime.document.blocks[-1].block is active


@pytest.mark.anyio
@pytest.mark.parametrize("markdown", ("```\nvalue", "```   \nvalue\n```"))
async def test_assistant_commit_renders_fenced_code_without_language(markdown: str) -> None:
    runtime = TuiRuntime()
    output = TuiOutputControl("", runtime=runtime, animate=False)

    await output.append_assistant_delta(markdown)
    await output.prepare_external_output()

    assert _document_text(runtime.document) == "• value"
    assert runtime.document.active_block is None


@pytest.mark.anyio
async def test_assistant_commit_falls_back_to_plain_text_after_markdown_failure() -> None:
    runtime = TuiRuntime()
    output = TuiOutputControl("", runtime=runtime, animate=False)
    presentation = TuiPresentationSink(output)

    await output.append_assistant_delta("```\nvalue\n```")
    with patch(
        "mind_app.tui.adapters.output.render_tui_markdown",
        side_effect=IndexError("invalid markdown"),
    ):
        await presentation.emit(build_failure_view("turn.failed", "request failed"))

    assert [item.kind for item in runtime.document.blocks] == ["assistant", "notice"]
    assert runtime.document.active_block is None
    rendered = _document_text(runtime.document)
    assert "```\n  value\n  ```" in rendered
    assert "request failed" in rendered


@pytest.mark.anyio
async def test_sources_are_assistant_metadata_instead_of_operation_output() -> None:
    runtime = TuiRuntime()
    output = TuiOutputControl("", runtime=runtime, animate=False)
    content = TuiContentSink(output)

    await content.emit(AssistantTextDelta("answer"))
    await content.emit(SourcesOutput(({
        "title": "Reference",
        "url": "https://example.com/reference",
    },)))

    assert [item.kind for item in runtime.document.blocks] == [
        "assistant",
        "assistant",
    ]
    assert [item.gap_before for item in runtime.document.blocks] == [
        False,
        True,
    ]
    assert runtime.document.active_block is None
    assert "Sources:" in _document_text(runtime.document)
    assert all(
        "bold" not in style
        for style, text in runtime.document.blocks[-1].block.fragments
        if text.strip()
    )
