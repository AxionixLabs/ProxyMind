# -*- coding: utf-8 -*-

import asyncio
from types import SimpleNamespace
from typing import get_args
from unittest.mock import (
    AsyncMock,
    Mock,
    PropertyMock,
    patch,
)

import pytest
from prompt_toolkit.data_structures import Size
from prompt_toolkit.input.defaults import create_pipe_input
from prompt_toolkit.output import DummyOutput
from prompt_toolkit.utils import get_cwidth

from mind_app.approval.models import ApprovalDecisionValue
from mind_core.design.terminal_capabilities import (
    TerminalCapabilities,
    TerminalColorLevel,
    TerminalIdentity,
    TerminalKind,
)
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
    TranscriptBlock,
    TuiBlockKind,
    TuiDocument,
)
from mind_app.tui.core.models import (
    FragmentBlock,
    LineFill,
    TranscriptBacktrackRequest,
)
from mind_app.tui.core.keymap import TuiRuntimeKeymap
from mind_app.tui.core.process_viewer import ProcessViewerRequest
from mind_app.tui.core.queued import TuiQueuedMessages, TuiSubmission
from mind_app.tui.core.render import (
    display_line_count,
    fragment_continuation_widths,
    fragments_text,
    join_formatted_lines,
    sanitize_formatted_text,
    split_formatted_lines,
)
from mind_app.tui.core.runtime import TuiRuntime
from mind_app.tui.core.screen import (
    FrameGeometry,
    _erase_terminal_scrollback,
)
from mind_app.tui.core.styles import (
    ASSISTANT_PREFIX_CLASS,
    failure_parts,
)


def _block(text: str) -> FragmentBlock:
    return FragmentBlock((("", text),))


def _document_text(document: TuiDocument) -> str:
    return "".join(text for _style, text in document.fragments(width=80))


def _transcript_text(document: TuiDocument) -> str:
    return "".join(
        text for _style, text in document.transcript_fragments(width=80)
    )


def test_full_width_line_tracks_terminal_width_without_wrapping() -> None:
    document = TuiDocument()
    document.append_block(
        FragmentBlock(
            (("class:rule", "─ Finished in 43s " + "─" * 60),),
            line_fill=LineFill(character="─"),
        ),
        kind="system",
    )

    for width in (80, 40, 64):
        fragments = document.fragments(width=width)
        text = fragments_text(fragments)

        assert "Finished in 43s" in text
        assert "\n" not in text
        assert get_cwidth(text) == width


async def _render_next_frame(runtime: TuiRuntime):
    """触发渲染并返回完成后的屏幕。"""
    previous_revision = runtime.screen.application.render_counter
    runtime.invalidate()
    for _ in range(20):
        await asyncio.sleep(0)
        if runtime.screen.application.render_counter > previous_revision:
            return runtime.screen.application.renderer.last_rendered_screen
    raise AssertionError("next frame was not rendered")


@pytest.mark.anyio
async def test_full_width_line_rerenders_in_one_row_after_resize() -> None:
    with create_pipe_input() as input_obj:
        output = _AlternateScreenOutput(columns=80, rows=12)
        runtime = TuiRuntime(input_obj=input_obj, output_obj=output)
        await runtime.open()
        try:
            runtime.append_block(
                FragmentBlock(
                    (("class:rule", "─ Finished in 43s " + "─" * 60),),
                    line_fill=LineFill(character="─"),
                ),
                kind="system",
            )
            await _render_next_frame(runtime)

            output.size = Size(rows=12, columns=40)
            await _render_next_frame(runtime)

            text = fragments_text(runtime.screen.transcript_fragments())
            assert get_cwidth(text) == 40
            assert runtime.screen._transcript_dimension().preferred == 1
        finally:
            await runtime.close()


@pytest.mark.anyio
async def test_resize_discards_old_canvas_height_floor() -> None:
    with create_pipe_input() as input_obj:
        output = _AlternateScreenOutput(columns=40, rows=12)
        runtime = TuiRuntime(input_obj=input_obj, output_obj=output)
        await runtime.open()
        try:
            runtime.append_block(_block("x" * 60), kind="assistant")
            await _render_next_frame(runtime)
            narrow_natural_height = runtime.screen._natural_visible_height()

            output.size = Size(rows=12, columns=80)
            screen = await _render_next_frame(runtime)
            positions = screen.visible_windows_to_write_positions

            assert runtime.screen._natural_visible_height() == (
                narrow_natural_height - 1
            )
            assert runtime.screen._visible_height() == (
                runtime.screen._natural_visible_height()
            )
            assert runtime.screen.canvas_spacer not in positions

            transcript = positions[runtime.screen.transcript_window]
            content_gap = positions[runtime.screen.content_input_gap.content]
            top_padding = positions[runtime.screen.input_top_padding]

            assert content_gap.ypos == transcript.ypos + transcript.height
            assert top_padding.ypos == content_gap.ypos + content_gap.height
        finally:
            await runtime.close()


def test_failure_parts_adds_non_bold_marker_and_text() -> None:
    marker, body = failure_parts("failed")

    assert marker.text == "■"
    assert body.text == " failed"
    assert not marker.style.bold
    assert not body.style.bold


class _AlternateScreenOutput(DummyOutput):
    def __init__(self, *, columns: int = 80, rows: int = 24) -> None:
        self.size = Size(rows=rows, columns=columns)
        self.enter_count = 0
        self.quit_count = 0

    def get_size(self) -> Size:
        return self.size

    def enter_alternate_screen(self) -> None:
        self.enter_count += 1

    def quit_alternate_screen(self) -> None:
        self.quit_count += 1


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


@pytest.mark.parametrize(
    "first_kind",
    tuple(kind for kind in get_args(TuiBlockKind) if kind != "user"),
)
@pytest.mark.parametrize(
    "second_kind",
    tuple(kind for kind in get_args(TuiBlockKind) if kind != "user"),
)
def test_document_separates_non_user_block_transition(
    first_kind: TuiBlockKind,
    second_kind: TuiBlockKind,
) -> None:
    document = TuiDocument()

    document.append_block(_block("first"), kind=first_kind)
    document.append_block(_block("second"), kind=second_kind)

    assert _document_text(document) == "first\n\nsecond"


def test_live_fragments_only_separate_visible_stable_content() -> None:
    visible_document = TuiDocument()
    visible_document.append_block(_block("stable"), kind="assistant")
    visible_document.set_active(_block("live"), kind="assistant")

    hidden_document = TuiDocument()
    hidden_document.append_block(_block("stable"), kind="assistant")
    hidden_document.commit_scrollback_prefix(1)
    hidden_document.set_active(_block("live"), kind="assistant")

    assert fragments_text(visible_document.live_fragments()) == "\n\nlive"
    assert fragments_text(hidden_document.live_fragments()) == "live"


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

    stored = document.blocks[0].display_block
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

    assert document.blocks[0].display_block is block
    assert document.blocks[0].display_block.fragments == block.fragments


def test_document_keeps_display_and_transcript_blocks_in_one_archive() -> None:
    document = TuiDocument()
    display = _block("short output")
    transcript = _block("full output\nline two")

    document.append_block(
        display,
        kind="operation",
        transcript_block=transcript,
    )

    assert _document_text(document) == "short output"
    assert _transcript_text(document) == "full output\nline two"
    assert document.blocks[0].display_block is display
    assert document.blocks[0].transcript_block is transcript


def test_document_refreshes_active_transcript_revision() -> None:
    document = TuiDocument()
    document.append_block(_block("stable"), kind="assistant")
    committed_cells = document.transcript_snapshot().committed_cells
    document.set_active(
        _block("running"),
        kind="operation",
        transcript_block=_block("$ command\nfirst"),
    )
    revision = document.transcript_revision

    document.set_active(
        _block("running"),
        kind="operation",
        transcript_block=_block("$ command\nfirst\nsecond"),
    )

    assert document.transcript_revision > revision
    snapshot = document.transcript_snapshot()
    assert snapshot.committed_cells is committed_cells
    assert snapshot.live_tail is not None
    assert not snapshot.live_tail.cells[0].transcript_stable
    assert _transcript_text(document) == "stable\n\n$ command\nfirst\nsecond"


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


def test_render_frame_uses_one_terminal_geometry_snapshot() -> None:
    runtime = TuiRuntime()
    runtime.screen._output_size = Mock(side_effect=[(40, 10), (72, 18)])

    runtime.screen.application.render_counter = 1
    runtime.screen.application.before_render.fire()

    assert runtime.screen.frame_geometry == FrameGeometry(40, 10, 1)
    assert runtime.screen.terminal_width == 40
    assert runtime.screen.terminal_height == 10
    assert runtime.viewport._observed_geometry == (40, 10)
    assert runtime.screen._output_size.call_count == 1

    runtime.screen.application.after_render.fire()
    assert runtime.screen._frame_geometry is None

    runtime.screen.application.render_counter = 2
    runtime.screen.application.before_render.fire()

    assert runtime.screen.frame_geometry == FrameGeometry(72, 18, 2)
    assert runtime.screen.terminal_width == 72
    assert runtime.screen.terminal_height == 18
    assert runtime.screen._output_size.call_count == 2


def test_geometry_refresh_reads_one_settled_size_snapshot() -> None:
    runtime = TuiRuntime()
    runtime.screen._output_size = Mock(return_value=(54, 16))

    runtime.viewport.refresh_geometry()

    assert runtime.viewport._observed_geometry == (54, 16)
    runtime.screen._output_size.assert_called_once_with()


def test_replace_transcript_resets_document_state() -> None:
    runtime = TuiRuntime()
    runtime.append_block(_block("old content"), kind="assistant")
    runtime.set_active_renderable(_block("running"), kind="operation")
    runtime.viewport.view_row = 4

    restored = (
        TranscriptBlock(
            display_block=_block("new user"),
            transcript_block=_block("new user"),
            kind="user",
            gap_before=True,
            turn_id="turn_restored",
            prompt="new user",
        ),
        TranscriptBlock(
            display_block=_block("new assistant"),
            transcript_block=_block("new assistant"),
            kind="assistant",
        ),
    )

    with patch.object(runtime.screen, "clear_terminal_scrollback") as clear:
        runtime.replace_transcript(restored)

    assert [item.kind for item in runtime.document.blocks] == [
        "user",
        "assistant",
    ]
    assert [item.gap_before for item in runtime.document.blocks] == [
        False,
        True,
    ]
    assert runtime.document.blocks[0].turn_id == "turn_restored"
    assert runtime.document.active_block is None
    assert runtime.document.scrollback_line_count == 0
    assert runtime.document.cleared_line_count == 0
    assert runtime.viewport.view_row is None
    assert "old content" not in _transcript_text(runtime.document)
    clear.assert_called_once_with()


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
                await asyncio.sleep(0.12)

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
async def test_width_resize_reflows_native_scrollback_from_document() -> None:
    with create_pipe_input() as pipe_input:
        runtime = TuiRuntime(input_obj=pipe_input, output_obj=DummyOutput())
        terminal_size = Size(rows=10, columns=40)
        source = "\n".join(f"entry {index:02d}" for index in range(60))

        with patch.object(
            runtime.screen.application.output,
            "get_size",
            side_effect=lambda: terminal_size,
        ):
            await runtime.open()
            try:
                runtime.append_block(_block(source), kind="assistant")
                await asyncio.sleep(0.02)
                assert runtime.document.scrollback_line_count > 0

                with patch.object(
                    runtime.screen,
                    "clear_terminal_scrollback",
                ) as clear, patch.object(
                    runtime.screen.application,
                    "print_text",
                    wraps=runtime.screen.application.print_text,
                ) as print_text:
                    terminal_size = Size(rows=10, columns=24)
                    runtime.viewport.observe_terminal_geometry(24, 10)
                    await asyncio.sleep(0.12)

                chunks = [
                    "".join(text for _style, text in call.args[0])
                    for call in print_text.call_args_list
                ]
                visible = _document_text(runtime.document)

                clear.assert_called_once_with()
                assert chunks
                assert "\n".join([*chunks, visible]) == source
                assert runtime.viewport._reflowed_geometry == (24, 10)
            finally:
                await runtime.close()


@pytest.mark.anyio
@pytest.mark.parametrize("defer_mode", ["overlay", "stream"])
async def test_width_resize_reflow_waits_for_transient_surface(
    defer_mode: str,
) -> None:
    with create_pipe_input() as pipe_input:
        runtime = TuiRuntime(input_obj=pipe_input, output_obj=DummyOutput())
        terminal_size = Size(rows=8, columns=40)

        with patch.object(
            runtime.screen.application.output,
            "get_size",
            side_effect=lambda: terminal_size,
        ):
            await runtime.open()
            try:
                runtime.append_block(
                    _block("\n".join(f"entry {index}" for index in range(40))),
                    kind="assistant",
                )
                await asyncio.sleep(0.02)
                assert runtime.document.scrollback_line_count > 0

                if defer_mode == "overlay":
                    runtime.toggle_transcript_overlay()
                else:
                    runtime.set_active_renderable(
                        _block("streaming"),
                        kind="assistant",
                    )

                with patch.object(
                    runtime.screen,
                    "clear_terminal_scrollback",
                ) as clear:
                    terminal_size = Size(rows=8, columns=24)
                    runtime.viewport.observe_terminal_geometry(24, 8)
                    await asyncio.sleep(0.12)
                    clear.assert_not_called()

                    if defer_mode == "overlay":
                        runtime.toggle_transcript_overlay()
                    else:
                        runtime.clear_active_renderable()
                    await asyncio.sleep(0.02)

                clear.assert_called_once_with()
                assert runtime.viewport._reflowed_geometry == (24, 8)
            finally:
                await runtime.close()


@pytest.mark.anyio
async def test_height_only_resize_reflows_native_scrollback() -> None:
    with create_pipe_input() as pipe_input:
        runtime = TuiRuntime(input_obj=pipe_input, output_obj=DummyOutput())
        terminal_size = Size(rows=8, columns=40)
        source = "\n".join(f"entry {index:02d}" for index in range(60))

        with patch.object(
            runtime.screen.application.output,
            "get_size",
            side_effect=lambda: terminal_size,
        ):
            await runtime.open()
            try:
                runtime.append_block(_block(source), kind="assistant")
                await asyncio.sleep(0.02)
                assert runtime.document.scrollback_line_count > 0

                with patch.object(
                    runtime.screen,
                    "clear_terminal_scrollback",
                ) as clear, patch.object(
                    runtime.screen.application,
                    "print_text",
                    wraps=runtime.screen.application.print_text,
                ) as print_text:
                    terminal_size = Size(rows=14, columns=40)
                    runtime.viewport.observe_terminal_geometry(40, 14)
                    await asyncio.sleep(0.12)

                clear.assert_called_once_with()
                assert runtime.viewport._reflowed_geometry == (40, 14)
                chunks = [
                    "".join(text for _style, text in call.args[0])
                    for call in print_text.call_args_list
                ]
                assert "\n".join([
                    *chunks,
                    _document_text(runtime.document),
                ]) == source
            finally:
                await runtime.close()


@pytest.mark.anyio
async def test_resize_rechecks_settled_geometry_before_reflow() -> None:
    with create_pipe_input() as pipe_input:
        runtime = TuiRuntime(input_obj=pipe_input, output_obj=DummyOutput())
        terminal_size = Size(rows=8, columns=40)

        with patch.object(
            runtime.screen.application.output,
            "get_size",
            side_effect=lambda: terminal_size,
        ):
            await runtime.open()
            try:
                runtime.append_block(
                    _block("\n".join(f"entry {index}" for index in range(60))),
                    kind="assistant",
                )
                await asyncio.sleep(0.02)

                with patch.object(
                    runtime.screen,
                    "clear_terminal_scrollback",
                ) as clear:
                    terminal_size = Size(rows=8, columns=24)
                    runtime.viewport.observe_terminal_geometry(24, 8)
                    terminal_size = Size(rows=12, columns=30)
                    await asyncio.sleep(0.2)

                clear.assert_called_once_with()
                assert runtime.viewport._observed_geometry == (30, 12)
                assert runtime.viewport._reflowed_geometry == (30, 12)
            finally:
                await runtime.close()


@pytest.mark.anyio
async def test_stream_commit_during_resize_reflows_only_final_geometry() -> None:
    with create_pipe_input() as pipe_input:
        runtime = TuiRuntime(input_obj=pipe_input, output_obj=DummyOutput())
        terminal_size = Size(rows=8, columns=40)
        stable_source = "\n".join(
            f"stable entry {index:02d}" for index in range(60)
        )
        stream_source = "$ command\n" + "stream output " * 12

        with patch.object(
            runtime.screen.application.output,
            "get_size",
            side_effect=lambda: terminal_size,
        ):
            await runtime.open()
            try:
                runtime.append_block(_block(stable_source), kind="assistant")
                await asyncio.sleep(0.02)
                assert runtime.document.scrollback_line_count > 0

                runtime.toggle_transcript_overlay()
                runtime.set_active_renderable(
                    _block("running"),
                    kind="operation",
                    transcript_block=_block(stream_source),
                )

                with patch.object(
                    runtime.screen,
                    "clear_terminal_scrollback",
                ) as clear:
                    for width, height in ((30, 9), (22, 11), (36, 12)):
                        terminal_size = Size(rows=height, columns=width)
                        runtime.viewport.observe_terminal_geometry(width, height)

                    runtime.commit_active_renderable(
                        _block("completed"),
                        transcript_block=_block(stream_source),
                    )
                    await asyncio.sleep(0.12)
                    clear.assert_not_called()

                    runtime.toggle_transcript_overlay()
                    await asyncio.sleep(0.12)

                clear.assert_called_once_with()
                assert runtime.viewport._reflowed_geometry == (36, 12)
                transcript = _transcript_text(runtime.document)
                assert transcript.count("$ command") == 1
                assert transcript.count("stream output") == 12
            finally:
                await runtime.close()


@pytest.mark.anyio
async def test_modal_return_refreshes_terminal_geometry() -> None:
    with create_pipe_input() as pipe_input:
        runtime = TuiRuntime(input_obj=pipe_input, output_obj=DummyOutput())
        terminal_size = Size(rows=10, columns=40)

        with patch.object(
            runtime.screen.application.output,
            "get_size",
            side_effect=lambda: terminal_size,
        ):
            await runtime.open()
            try:
                async def external_program() -> str:
                    nonlocal terminal_size
                    terminal_size = Size(rows=16, columns=32)
                    return "done"

                assert await runtime.run_modal(external_program) == "done"
                assert runtime.viewport._observed_geometry == (32, 16)
                await asyncio.sleep(0.12)
                assert runtime.viewport._reflowed_geometry == (32, 16)
            finally:
                await runtime.close()


@pytest.mark.anyio
async def test_scrollback_reflow_uses_configured_line_limit() -> None:
    with create_pipe_input() as pipe_input:
        runtime = TuiRuntime(input_obj=pipe_input, output_obj=DummyOutput())
        runtime.configure_scrollback_reflow_line_limit(7)
        terminal_size = Size(rows=8, columns=40)

        with patch.object(
            runtime.screen.application.output,
            "get_size",
            side_effect=lambda: terminal_size,
        ):
            await runtime.open()
            try:
                runtime.append_block(
                    _block("\n".join(f"entry {index}" for index in range(60))),
                    kind="assistant",
                )
                await asyncio.sleep(0.02)

                with patch.object(
                    runtime.document,
                    "rewind_scrollback",
                    wraps=runtime.document.rewind_scrollback,
                ) as rewind:
                    terminal_size = Size(rows=12, columns=40)
                    runtime.viewport.observe_terminal_geometry(40, 12)
                    await asyncio.sleep(0.12)

                rewind.assert_called_once_with(max_line_count=7)
            finally:
                await runtime.close()


def test_scrollback_reflow_keeps_clear_boundary_and_caps_replay() -> None:
    document = TuiDocument()
    document.append_block(
        _block("\n".join(f"old {index}" for index in range(20))),
        kind="assistant",
    )
    document.commit_scrollback_prefix(20)
    document.clear_visible_prefix()
    document.append_block(
        _block("\n".join(f"new {index}" for index in range(20))),
        kind="assistant",
    )
    document.commit_scrollback_prefix(21)

    document.rewind_scrollback(max_line_count=5)

    assert document.scrollback_line_count == len(document._stable_lines) - 5
    assert document.scrollback_line_count >= document.cleared_line_count
    assert "old 19" not in "".join(
        text for line in document.visible_stable_lines() for _style, text in line
    )


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
                render_count = runtime.screen.application.render_counter
                for index in range(6):
                    runtime.append_block(
                        _block(f"block {index}\n" + "line\n" * 3),
                        kind="operation",
                    )

                for _ in range(20):
                    await asyncio.sleep(0)
                    if runtime.screen.application.render_counter > render_count:
                        break

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
async def test_stable_commit_waits_for_render_before_scrollback() -> None:
    with create_pipe_input() as pipe_input:
        runtime = TuiRuntime(input_obj=pipe_input, output_obj=DummyOutput())

        with patch.object(
            runtime.screen.application.output,
            "get_size",
            return_value=Size(rows=10, columns=40),
        ):
            await runtime.open()
            try:
                block = _block("line\n" * 20)
                runtime.set_active_renderable(block, kind="assistant")

                for _ in range(20):
                    await asyncio.sleep(0)
                    if runtime.screen.application.render_counter >= 2:
                        break

                runtime.commit_active_renderable(block)

                assert runtime.viewport.scrollback_task is None

                for _ in range(50):
                    await asyncio.sleep(0.002)
                    if runtime.document.scrollback_line_count > 0:
                        break

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
                for _ in range(50):
                    await asyncio.sleep(0.002)
                    if (
                        runtime.screen.application.render_counter > render_count
                        and runtime.document.scrollback_line_count
                        > committed_count
                    ):
                        break

                assert runtime.document.scrollback_line_count > committed_count
                assert len(runtime.document.blocks) == 3
            finally:
                await runtime.close()


@pytest.mark.anyio
async def test_startup_title_uses_external_gap_and_colored_input_padding() -> None:
    with create_pipe_input() as pipe_input:
        runtime = TuiRuntime(input_obj=pipe_input, output_obj=DummyOutput())

        with patch.object(
            runtime.screen.application.output,
            "get_size",
            return_value=Size(rows=12, columns=40),
        ):
            await runtime.open()
            try:
                runtime.append_block(_block(">_ App (v1.0)"), kind="system")
                screen = await _render_next_frame(runtime)
                positions = screen.visible_windows_to_write_positions
                transcript = positions[runtime.screen.transcript_window]
                content_gap = positions[runtime.screen.content_input_gap.content]
                top_padding = positions[runtime.screen.input_top_padding]
                input_position = positions[runtime.screen.input.window]
                bottom_padding = positions[runtime.screen.input_bottom_padding]
                footer = positions[runtime.screen.footer_window]

                assert content_gap.ypos == transcript.ypos + transcript.height
                assert top_padding.ypos == content_gap.ypos + content_gap.height
                assert input_position.ypos == top_padding.ypos + top_padding.height
                assert bottom_padding.ypos == input_position.ypos + input_position.height
                assert footer.ypos == bottom_padding.ypos + bottom_padding.height
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
async def test_submission_handoff_never_renders_an_empty_intermediate_frame() -> None:
    with create_pipe_input() as pipe_input:
        runtime = TuiRuntime(input_obj=pipe_input, output_obj=DummyOutput())

        with patch.object(
            runtime.screen.application.output,
            "get_size",
            return_value=Size(rows=12, columns=40),
        ):
            frames: list[tuple[str, str, int]] = []

            def capture_frame(_application) -> None:
                frames.append((
                    runtime.screen.input.buffer.text,
                    _document_text(runtime.document),
                    runtime.screen.application.render_counter,
                ))

            runtime.screen.application.after_render += capture_frame

            await runtime.open()
            try:
                prompt_task = asyncio.create_task(runtime.read_message(
                    PromptContext(model="test")
                ))

                runtime.screen.input.buffer.text = "first\nsecond\nthird"

                for _ in range(20):
                    await asyncio.sleep(0)
                    if any(input_text for input_text, _text, _revision in frames):
                        break

                start = len(frames)
                runtime.screen.input.buffer.validate_and_handle()
                runtime.screen.input.buffer.validate_and_handle()

                assert await prompt_task == "first\nsecond\nthird"
                assert runtime.submissions.message_queue.empty()

                for _ in range(20):
                    await asyncio.sleep(0)
                    if _document_text(runtime.document):
                        break

                transition = frames[start:]
                assert transition
                assert not any(
                    not input_text and not document_text
                    for input_text, document_text, _revision in transition
                )
            finally:
                await runtime.close()


@pytest.mark.anyio
async def test_final_markdown_height_change_keeps_input_anchor() -> None:
    with create_pipe_input() as pipe_input:
        runtime = TuiRuntime(input_obj=pipe_input, output_obj=DummyOutput())

        with patch.object(
            runtime.screen.application.output,
            "get_size",
            return_value=Size(rows=12, columns=40),
        ):
            await runtime.open()
            try:
                output = TuiOutputControl("", runtime=runtime, animate=False)
                await output.append_assistant_delta("```\nvalue\n```")

                for _ in range(20):
                    await asyncio.sleep(0)
                    position = (
                        runtime.screen.application.renderer.last_rendered_screen
                        .visible_windows_to_write_positions.get(
                            runtime.screen.input.window
                        )
                    )
                    if position is not None and position.ypos == 5:
                        break

                streamed_position = (
                    runtime.screen.application.renderer.last_rendered_screen
                    .visible_windows_to_write_positions[
                        runtime.screen.input.window
                    ]
                )

                await output.settle_stream()

                previous_revision = runtime.screen.application.render_counter
                for _ in range(20):
                    await asyncio.sleep(0)
                    if runtime.screen.application.render_counter > previous_revision:
                        break

                settled_position = (
                    runtime.screen.application.renderer.last_rendered_screen
                    .visible_windows_to_write_positions[
                        runtime.screen.input.window
                    ]
                )

                assert settled_position.ypos == streamed_position.ypos
            finally:
                await runtime.close()


@pytest.mark.anyio
@pytest.mark.parametrize("decision", ["accept", "decline"])
async def test_approval_dismissal_returns_to_natural_canvas(
    decision: ApprovalDecisionValue,
) -> None:
    with create_pipe_input() as pipe_input:
        runtime = TuiRuntime(input_obj=pipe_input, output_obj=DummyOutput())

        with patch.object(
            runtime.screen.application.output,
            "get_size",
            return_value=Size(rows=12, columns=40),
        ):
            await runtime.open()
            try:
                runtime.set_active_renderable(_block("answer"), kind="assistant")

                approval_task = asyncio.create_task(runtime.request_approval({
                    "tool": "shell_command",
                    "command": "\n".join(
                        f"echo line-{index}" for index in range(20)
                    ),
                    "show_timer": False,
                }))

                for _ in range(20):
                    await asyncio.sleep(0)
                    screen = (
                        runtime.screen.application.renderer.last_rendered_screen
                    )
                    if (
                        runtime.screen.approval.active
                        and runtime.screen.approval_window
                        in screen.visible_windows_to_write_positions
                    ):
                        break

                runtime.screen.approval.finish(decision)
                assert await approval_task == decision

                previous_revision = runtime.screen.application.render_counter
                runtime.invalidate()
                for _ in range(20):
                    await asyncio.sleep(0)
                    if runtime.screen.application.render_counter > previous_revision:
                        break

                screen = runtime.screen.application.renderer.last_rendered_screen
                footer_position = screen.visible_windows_to_write_positions[
                    runtime.screen.footer_window
                ]

                assert runtime.screen.canvas_spacer not in (
                    screen.visible_windows_to_write_positions
                )
                assert runtime.screen._visible_height() == (
                    runtime.screen._natural_visible_height()
                )
                assert footer_position.ypos + footer_position.height == (
                    runtime.screen._visible_height()
                )
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
async def test_ctrl_t_opens_and_closes_full_transcript_overlay() -> None:
    with create_pipe_input() as pipe_input:
        output = _AlternateScreenOutput(columns=72, rows=18)
        runtime = TuiRuntime(input_obj=pipe_input, output_obj=output)
        runtime.append_block(
            _block("compact"),
            kind="operation",
            transcript_block=_block("full output"),
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
            assert rendered_text.splitlines()[0].startswith(
                "/ T R A N S C R I P T"
            )
            assert "full output" in rendered_text
            assert "Esc/Q/Ctrl+C/Ctrl+T to quit" in rendered_text
            assert "100%" in rendered_text
            assert any(
                "─" in line and "100%" in line
                for line in rendered_text.splitlines()
            )
            assert "full output" in "".join(
                text
                for _style, text in runtime.screen.transcript_overlay.fragments()
            )
            assert runtime.screen.application.layout.current_control == (
                runtime.screen.transcript_overlay_control
            )

            pipe_input.send_text("r")
            for _ in range(100):
                await asyncio.sleep(0.01)
                if runtime.screen.transcript_overlay.raw_mode:
                    break

            assert runtime.screen.transcript_overlay.raw_mode

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
async def test_ctrl_t_over_process_viewer_tracks_active_output() -> None:
    with create_pipe_input() as pipe_input:
        runtime = TuiRuntime(input_obj=pipe_input, output_obj=DummyOutput())
        await runtime.open()
        try:
            viewer = runtime.begin_process_viewer(
                ProcessViewerRequest(fragments=(("", " "),), max_height=1),
                _block("running"),
                transcript_block=_block("$ command\nlive output"),
            )
            assert runtime.screen.application.layout.current_control == (
                runtime.screen.process_viewer_control
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

            runtime.update_process_viewer(
                _block("running"),
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
                runtime.screen.process_viewer_control
            )

            runtime.resolve_process_viewer("done")
            assert await viewer == "done"
            runtime.commit_process_viewer(
                _block("completed"),
                transcript_block=_block("$ command\ncomplete output"),
            )
            assert runtime.screen.application.layout.current_control == (
                runtime.screen.input.control
            )
        finally:
            await runtime.close()


@pytest.mark.anyio
async def test_process_completion_keeps_transcript_screen_focused() -> None:
    with create_pipe_input() as pipe_input:
        runtime = TuiRuntime(input_obj=pipe_input, output_obj=DummyOutput())
        await runtime.open()
        try:
            viewer = runtime.begin_process_viewer(
                ProcessViewerRequest(fragments=(("", " "),), max_height=1),
                _block("running"),
                transcript_block=_block("$ command\nlive output"),
            )
            runtime.toggle_transcript_overlay()

            runtime.resolve_process_viewer("done")
            assert await viewer == "done"
            runtime.commit_process_viewer(
                _block("completed"),
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

    runtime.toggle_transcript_overlay()

    assert not runtime.screen.transcript_overlay.active
    assert runtime.screen.bottom_pane.active_surface == surface


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

    screen._enter_transcript_screen()
    renderer._in_alternate_screen = True

    with patch.object(
        renderer.output,
        "quit_alternate_screen",
        side_effect=OSError("terminal unavailable"),
    ), pytest.raises(OSError, match="terminal unavailable"):
        screen._leave_transcript_screen()

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


def test_transcript_overlay_switches_between_rich_and_raw_cells() -> None:
    runtime = TuiRuntime()
    runtime.append_block(
        _block("compact"),
        kind="operation",
        transcript_block=_block("Ran shell_command\n$ npm install"),
        raw_text="npm install",
    )
    runtime.toggle_transcript_overlay()
    overlay = runtime.screen.transcript_overlay

    assert "".join(text for _style, text in overlay.fragments()) == (
        "Ran shell_command\n$ npm install"
    )

    overlay.toggle_raw_mode()

    assert overlay.raw_mode
    assert "".join(text for _style, text in overlay.fragments()) == "npm install"
    assert all(not style for style, _text in overlay.fragments())

    overlay.toggle_raw_mode()

    assert not overlay.raw_mode
    assert "Ran shell_command" in "".join(
        text for _style, text in overlay.fragments()
    )


@pytest.mark.anyio
async def test_transcript_overlay_keeps_stream_source_text_for_raw_mode() -> None:
    runtime = TuiRuntime()
    output = TuiOutputControl("", runtime=runtime, animate=False)

    await output.append_assistant_delta("**bold** and `code`")
    await output.prepare_external_output()
    runtime.toggle_transcript_overlay()
    overlay = runtime.screen.transcript_overlay

    rich = "".join(text for _style, text in overlay.fragments())
    overlay.toggle_raw_mode()
    raw = "".join(text for _style, text in overlay.fragments())

    assert "**" not in rich
    assert "`" not in rich
    assert raw == "**bold** and `code`"


@pytest.mark.anyio
async def test_markdown_hyperlink_survives_overlay_wrap_and_raw_mode() -> None:
    capabilities = TerminalCapabilities(
        TerminalIdentity(TerminalKind.ITERM2, "iTerm2"),
        TerminalColorLevel.TRUECOLOR,
    )
    runtime = TuiRuntime(terminal_capabilities=capabilities)
    runtime.screen._output_size = lambda: (20, 10)
    output = TuiOutputControl("", runtime=runtime, animate=False)

    await output.append_assistant_delta(
        "[documentation-link-that-wraps](https://example.com/docs)"
    )
    await output.prepare_external_output()

    cell_fragments = runtime.document.blocks[-1].display_block.fragments
    assert fragments_text(cell_fragments) == "• documentation-link-that-wraps"
    assert any(
        style == "[ZeroWidthEscape]"
        and "https://example.com/docs" in text
        for style, text in cell_fragments
    )
    assert any(
        style == "[ZeroWidthEscape]"
        for style, _text in runtime.document.scrollback_prefix_fragments(1)
    )

    runtime.toggle_transcript_overlay()
    overlay = runtime.screen.transcript_overlay
    rich = overlay.fragments()

    assert len(split_formatted_lines(rich)) > 1
    assert fragments_text(rich).replace("\n  ", "") == (
        "• documentation-link-that-wraps"
    )
    assert sum(
        style == "[ZeroWidthEscape]"
        for style, _text in rich
    ) == 2

    overlay.toggle_raw_mode()
    raw = overlay.fragments()

    assert fragments_text(raw).replace("\n", "") == (
        "[documentation-link-that-wraps](https://example.com/docs)"
    )
    assert all(style != "[ZeroWidthEscape]" for style, _text in raw)


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


def test_transcript_overlay_live_tail_tracks_animation_tick() -> None:
    runtime = TuiRuntime()
    runtime.append_block(_block("stable"), kind="assistant")
    activity = _block("Thinking frame")
    runtime.screen.set_activity_renderable(activity)

    document_snapshot = runtime.document.transcript_snapshot()
    screen_snapshot = runtime.screen._transcript_snapshot()

    assert document_snapshot.live_tail is None
    assert screen_snapshot.live_tail is not None
    assert not screen_snapshot.live_tail.cells[-1].transcript_stable

    runtime.toggle_transcript_overlay()
    overlay = runtime.screen.transcript_overlay
    assert "Thinking frame" in "".join(
        text for _style, text in overlay.fragments()
    )
    first_key = overlay._cached_live_tail_key

    runtime.screen.set_activity_renderable(activity)
    overlay.fragments()
    second_key = overlay._cached_live_tail_key

    assert first_key is not None
    assert second_key is not None
    assert second_key.width == first_key.width
    assert second_key.revision == first_key.revision
    assert second_key.animation_tick != first_key.animation_tick

    runtime.screen.set_activity_renderable(_block("Thinking next frame"))
    assert "Thinking next frame" in "".join(
        text for _style, text in overlay.fragments()
    )

    runtime.screen.clear_activity_renderable()
    assert "Thinking next frame" not in "".join(
        text for _style, text in overlay.fragments()
    )
    assert overlay._cached_live_tail_key is None


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


def test_transcript_overlay_search_uses_raw_text_and_survives_reflow() -> None:
    runtime = TuiRuntime()
    output_size = [32, 10]
    runtime.screen._output_size = lambda: tuple(output_size)
    runtime.append_block(
        _block("rendered first"),
        kind="assistant",
        raw_text="**Needle** in original markdown",
    )
    for index in range(8):
        runtime.append_block(_block(f"filler {index}"), kind="assistant")
    runtime.append_block(
        _block("rendered second"),
        kind="operation",
        raw_text="tool output contains needle",
    )
    runtime.toggle_transcript_overlay()
    overlay = runtime.screen.transcript_overlay
    overlay.jump_top()

    overlay.begin_search()
    overlay.append_search_text("nEeDlE")
    assert overlay.confirm_search()
    assert overlay.search_result_position == (1, 2)
    assert overlay.scroll_offset == 0
    assert "1/2 nEeDlE" in fragments_text(
        runtime.screen._transcript_overlay_primary_help_fragments()
    )
    assert any(
        "class:transcript.overlay.search-match" in style
        for style, _text in overlay.visible_fragments()
    )

    assert overlay.step_search(1)
    second_offset = overlay.scroll_offset
    assert second_offset > 0
    assert overlay.search_result_position == (2, 2)

    overlay.toggle_raw_mode()
    output_size[0] = 18
    overlay.visible_fragments()
    assert overlay.search_result_position == (2, 2)
    assert overlay.scroll_offset <= overlay._max_scroll_offset()

    runtime.append_block(
        _block("rendered third"),
        kind="assistant",
        raw_text="new needle result",
    )
    assert overlay.search_result_position == (2, 3)

    runtime.set_active_renderable(
        _block("live needle is not committed"),
        kind="assistant",
    )
    assert overlay.search_result_position == (2, 3)
    assert overlay.step_search(-1)
    assert overlay.search_result_position == (1, 3)

    runtime.commit_active_renderable(_block("committed live needle"))
    assert overlay.search_result_position == (1, 4)


def test_transcript_search_recovers_when_backtrack_removes_current_match() -> None:
    runtime = TuiRuntime()
    runtime.append_block(_block("first prompt"), kind="user")
    assert runtime.bind_submitted_turn("turn_one", "first prompt")
    runtime.append_block(
        _block("first needle result"),
        kind="assistant",
    )
    runtime.append_block(_block("second prompt"), kind="user")
    assert runtime.bind_submitted_turn("turn_two", "second prompt")
    runtime.append_block(
        _block("second needle result"),
        kind="assistant",
    )
    runtime.toggle_transcript_overlay()
    overlay = runtime.screen.transcript_overlay

    overlay.begin_search()
    overlay.append_search_text("needle")
    assert overlay.confirm_search()
    assert overlay.step_search(1)
    assert overlay.search_result_position == (2, 2)

    assert runtime.apply_transcript_backtrack(
        TranscriptBacktrackRequest(
            turn_id="turn_two",
            prompt="second prompt revised",
        )
    )

    assert overlay.search_query == "needle"
    assert overlay.search_result_position == (1, 1)
    assert runtime.screen.input.buffer.text == "second prompt revised"


def test_transcript_overlay_search_cancel_and_empty_result_state() -> None:
    runtime = TuiRuntime()
    runtime.append_block(_block("only content"), kind="assistant")
    runtime.toggle_transcript_overlay()
    overlay = runtime.screen.transcript_overlay

    overlay.begin_search()
    overlay.append_search_text("missin👩\u200d💻")
    overlay.backspace_search()
    assert overlay.search_query == "missin"
    overlay.cancel_search()
    assert not overlay.search_editing
    assert overlay.search_query == ""

    overlay.begin_search()
    overlay.append_search_text("absent")
    assert not overlay.confirm_search()
    assert overlay.search_result_position == (0, 0)
    assert not overlay.step_search(1)


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
        assert runtime.document.discard_trailing_block(
            stable_cells[-1].display_block
        )
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
async def test_submission_commit_and_rollback_notify_open_transcript() -> None:
    runtime = TuiRuntime()
    runtime.screen._output_size = lambda: (40, 10)
    runtime.append_block(
        _block("\n".join(f"line {index}" for index in range(20))),
        kind="assistant",
    )
    runtime.toggle_transcript_overlay()
    overlay = runtime.screen.transcript_overlay

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
    assert overlay.follow_bottom
    assert "new question" in "".join(
        text for _style, text in overlay.visible_fragments()
    )

    with patch.object(
        overlay,
        "content_changed",
        wraps=overlay.content_changed,
    ) as content_changed:
        runtime._discard_submitted_query()

    assert content_changed.call_count == 1
    assert "new question" not in "".join(
        text for _style, text in overlay.fragments()
    )
    assert overlay.follow_bottom


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


@pytest.mark.anyio
async def test_presentation_separates_consecutive_tool_groups() -> None:
    runtime = TuiRuntime()
    output = TuiOutputControl("", runtime=runtime, animate=False)
    presentation = TuiPresentationSink(output)

    await output.append_assistant_delta("model output")
    await output.prepare_external_output()
    first_view = build_tool_start_view(
        "shell_command",
        {"command": "echo one"},
        call_id="one",
    )
    await presentation.emit(first_view)
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
    assert runtime.document.blocks[1].source is first_view
    assert runtime.document.blocks[1].raw_text == "echo one"
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
async def test_generic_tool_result_has_compact_hint_and_full_transcript() -> None:
    runtime = TuiRuntime(keymap=TuiRuntimeKeymap.from_config({
        "tui": {
            "keymap": {
                "global": {"open_transcript": "f12"},
            }
        }
    }))
    output = TuiOutputControl("", runtime=runtime, animate=False)
    presentation = TuiPresentationSink(output)
    result = "\n".join(f"result line {index}" for index in range(80))

    await presentation.emit(build_generic_tool_result_view(
        "remote_tool",
        result,
        ok=True,
        call_id="long-result",
    ))

    display = _document_text(runtime.document)
    transcript = _transcript_text(runtime.document)
    assert "(F12 to view transcript)" in display
    assert "result line 0" in transcript
    assert "result line 79" in transcript
    assert "(F12 to view transcript)" not in transcript


@pytest.mark.anyio
async def test_native_shell_result_transcript_keeps_command_and_output() -> None:
    runtime = TuiRuntime()
    output = TuiOutputControl("", runtime=runtime, animate=False)
    presentation = TuiPresentationSink(output)
    command = "Get-ChildItem\n| Select-Object -First 1"
    output_lines = [f"output line {index}" for index in range(80)]

    await presentation.emit(build_native_tool_result_view(
        "shell_command",
        {"command": command},
        ok=True,
        data={
            "command": command,
            "output_lines": output_lines,
        },
        call_id="long-shell",
    ))

    display = _document_text(runtime.document)
    transcript = _transcript_text(runtime.document)
    assert "(Ctrl+T to view transcript)" in display
    assert f"$ {command}" in transcript
    assert output_lines[0] in transcript
    assert output_lines[-1] in transcript


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
async def test_animated_stream_cursor_stays_until_stream_settles() -> None:
    runtime = TuiRuntime()
    output = TuiOutputControl("", runtime=runtime, animate=True)
    output._cursor = "█"

    await output.append_assistant_delta("done")

    assert output.assistant.pending_length == 0
    assert _document_text(runtime.document) == "• done█"

    await asyncio.sleep(0.12)

    assert _document_text(runtime.document) == "• done█"

    await output.settle_stream()

    assert _document_text(runtime.document) == "• done"


@pytest.mark.anyio
async def test_new_delta_continues_active_stream_cursor() -> None:
    runtime = TuiRuntime()
    output = TuiOutputControl("", runtime=runtime, animate=True)
    output._cursor = "█"

    await output.append_assistant_delta("done")
    await asyncio.sleep(0.12)

    await output.append_assistant_delta(" next")

    assert _document_text(runtime.document).endswith("█")

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

    fragments = runtime.document.blocks[-1].display_block.fragments
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

    assert runtime.document.blocks[-1].display_block is active


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
        for style, text in runtime.document.blocks[-1].display_block.fragments
        if text.strip()
    )
