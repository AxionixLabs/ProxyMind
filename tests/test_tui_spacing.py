# -*- coding: utf-8 -*-

import asyncio
import io
import typing
from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import get_args
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

from mind_app.approval.models import ApprovalDecisionValue
from mind_core.design.terminal_capabilities import (
    TerminalCapabilities,
    TerminalColorLevel,
    TerminalIdentity,
    TerminalKind,
)
from mind_app.interaction.contracts import PromptContext
from mind_app.output.content import (
    AssistantOutputBoundary,
    AssistantSegmentCompleted,
    AssistantTextDelta,
    SourcesOutput,
)
from mind_app.presentation.approval_views import build_approval_view
from mind_app.presentation.lifecycle_views import build_failure_view
from mind_app.presentation.models import (
    NativeToolResultView,
    PlanItemView,
    PlanUpdateView,
    ToolStartView,
)
from mind_app.presentation.terminal_text import sanitize_terminal_text
from mind_app.presentation.tool_views import (
    build_generic_tool_result_view,
    build_native_tool_result_view,
    build_tool_start_view,
)
from mind_app.tui.adapters.content import TuiContentSink
from mind_app.tui.adapters import markdown as tui_markdown
from mind_app.tui.adapters.markdown import (
    TuiMarkdownStreamRenderer,
    render_tui_markdown,
)
from mind_app.tui.adapters.output import TuiOutputControl
from mind_app.tui.adapters.presentation import TuiPresentationSink
from mind_app.tui.core.document import (
    TranscriptBlock,
    TuiBlockKind,
    TuiDocument,
)
from mind_app.tui.core.models import (
    FragmentBlock,
    LineFill,
    MenuOption,
    MenuRequest,
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
    wrap_formatted_lines,
)
from mind_app.tui.core.runtime import TuiRuntime
from mind_app.tui.features.processes import (
    PROCESS_VIEWER_FOCUS_REQUEST,
    exec_session_live_block,
    exec_session_summary_block,
)
from mind_app.tui.core.screen import (
    FrameGeometry,
    _clear_terminal_for_resize_replay,
    _erase_terminal_scrollback,
    _set_alternate_scroll_mode,
    _set_synchronized_output,
)
from mind_app.tui.core.styles import (
    ASSISTANT_PREFIX_CLASS,
    assistant_block,
    failure_parts,
    query_block,
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


async def _wait_for_input_text(runtime: TuiRuntime, text: str) -> None:
    """等待管道输入被主输入框完整消费。"""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + 1.0

    while loop.time() < deadline:
        if runtime.screen.input.buffer.text == text:
            return None
        await asyncio.sleep(0.001)

    raise AssertionError(
        "input text did not become "
        f"{text!r}: {runtime.screen.input.buffer.text!r}"
    )


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


class _KnownInlineHeightOutput(_AlternateScreenOutput):
    def __init__(
        self,
        *,
        columns: int = 80,
        rows: int = 24,
        available_rows: int,
    ) -> None:
        super().__init__(columns=columns, rows=rows)
        self.available_rows = available_rows

    def get_rows_below_cursor_position(self) -> int:
        return self.available_rows


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


def test_assistant_block_keeps_whitespace_only_rows_structurally_empty() -> None:
    block = assistant_block(_block("first\n   \nsecond"))
    lines = split_formatted_lines(list(block.fragments))

    assert fragments_text(block.fragments) == "• first\n\n  second"
    assert lines[1] == []


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


def test_live_fragments_preserve_native_scrollback_boundary() -> None:
    visible_document = TuiDocument()
    visible_document.append_block(_block("stable"), kind="assistant")
    visible_document.set_active(_block("live"), kind="assistant")

    scrolled_document = TuiDocument()
    scrolled_document.append_block(_block("stable"), kind="assistant")
    scrolled_document.commit_scrollback_prefix(1, expected_start=0)
    scrolled_document.set_active(_block("live"), kind="assistant")

    cleared_document = TuiDocument()
    cleared_document.append_block(_block("stable"), kind="assistant")
    cleared_document.clear_visible_prefix()
    cleared_document.set_active(_block("live"), kind="assistant")

    assert fragments_text(visible_document.live_fragments()) == "\n\nlive"
    assert fragments_text(scrolled_document.live_fragments()) == "\nlive"
    assert fragments_text(cleared_document.live_fragments()) == "live"


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


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("gap_before", "expected_gap"),
    [(None, "\n\n"), (2, "\n\n\n")],
)
async def test_process_viewer_keeps_requested_spacing_before_title(
    gap_before: int | None,
    expected_gap: str,
) -> None:
    runtime = TuiRuntime()
    runtime.append_block(_block("Finished"), kind="system")

    viewer = runtime.begin_process_viewer(
        ProcessViewerRequest(fragments=(("", " "),), max_height=1),
        _block("Exec running"),
        gap_before=gap_before,
    )

    assert _document_text(runtime.document) == f"Finished{expected_gap}Exec running"

    runtime.update_process_viewer(_block("Exec updated"), gap_before=gap_before)
    assert _document_text(runtime.document) == f"Finished{expected_gap}Exec updated"

    runtime.resolve_process_viewer("done")
    assert await viewer == "done"
    runtime.commit_process_viewer(_block("Exec complete"))

    assert _document_text(runtime.document) == f"Finished{expected_gap}Exec complete"


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

    document.commit_scrollback_prefix(2, expected_start=0)

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

    document.commit_scrollback_prefix(1, expected_start=4)

    assert document.scrollback_line_count == 5
    assert "".join(
        text for _style, text in document.all_fragments(width=80)
    ) == "first\n\nsecond\n\nthird"


def test_scrollback_keeps_gaps_between_consecutive_visual_blocks() -> None:
    document = TuiDocument()
    document.append_block(_block("reply"), kind="assistant")
    document.commit_scrollback_prefix(1, expected_start=0)

    document.append_block(_block("Started command"), kind="operation")
    assert _document_text(document) == "\nStarted command"
    document.commit_scrollback_prefix(2, expected_start=1)

    document.append_block(_block("/ps output"), kind="operation")
    assert _document_text(document) == "\n/ps output"
    document.commit_scrollback_prefix(2, expected_start=3)

    document.set_active(_block("assistant reply"), kind="assistant")
    assert _document_text(document) == "\nassistant reply"
    assert fragments_text(document.live_fragments()) == "\nassistant reply"
    document.commit_active(_block("assistant reply"))
    document.commit_scrollback_prefix(2, expected_start=5)

    document.append_block(_block("Wrote stdin"), kind="operation")

    assert _document_text(document) == "\nWrote stdin"
    assert fragments_text(document.all_fragments(width=80)) == (
        "reply\n\nStarted command\n\n/ps output\n\n"
        "assistant reply\n\nWrote stdin"
    )


def test_scrollback_prefix_advances_only_to_complete_block_boundaries() -> None:
    document = TuiDocument()
    document.append_block(_block("first 0\nfirst 1\nfirst 2"), kind="assistant")
    document.append_block(_block("second 0\nsecond 1"), kind="operation")
    document.append_block(_block("third"), kind="assistant")

    assert document.complete_scrollback_prefix_line_count(
        required_line_count=2,
        maximum_line_count=8,
    ) == 3
    assert document.complete_scrollback_prefix_line_count(
        required_line_count=4,
        maximum_line_count=8,
    ) == 6
    assert document.complete_scrollback_prefix_line_count(
        required_line_count=4,
        maximum_line_count=5,
    ) == 3

    document.commit_scrollback_prefix(3, expected_start=0)

    assert document.complete_scrollback_prefix_line_count(
        required_line_count=1,
        maximum_line_count=5,
    ) == 3


def test_scrollback_commit_rejects_changed_visible_prefix() -> None:
    document = TuiDocument()
    document.append_block(_block("first\nsecond"), kind="assistant")

    assert not document.commit_scrollback_prefix(1, expected_start=1)
    assert document.scrollback_line_count == 0
    assert document.commit_scrollback_prefix(1, expected_start=0)
    assert document.scrollback_line_count == 1


def test_scrollback_block_boundary_keeps_submitted_query_visible() -> None:
    runtime = TuiRuntime()
    runtime.append_block(_block("prior 0\nprior 1"), kind="assistant")
    runtime.append_block(_block("new query"), kind="user")
    query = runtime.document.blocks[-1].display_block
    runtime.viewport.mark_submitted_query(query)

    with (
        patch.object(runtime.viewport, "_get_available_height", return_value=1),
        patch.object(runtime.viewport, "_get_terminal_width", return_value=40),
    ):
        line_count = runtime.viewport._scrollback_prefix_line_count()

    assert line_count == 2
    assert fragments_text(
        runtime.document.scrollback_prefix_fragments(line_count)
    ) == "prior 0\nprior 1"


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


def test_resize_replay_clears_visible_screen_and_scrollback() -> None:
    output = SimpleNamespace(
        vt100_output=object(),
        write_raw=Mock(),
        flush=Mock(),
    )

    _clear_terminal_for_resize_replay(output)

    output.write_raw.assert_called_once_with(
        "\x1b[r\x1b[0m\x1b[H\x1b[2J\x1b[3J\x1b[H"
    )
    output.flush.assert_not_called()


def test_resize_replay_pairs_synchronized_output_sequences() -> None:
    output = SimpleNamespace(
        vt100_output=object(),
        write_raw=Mock(),
        flush=Mock(),
    )

    assert _set_synchronized_output(output, True) is True
    assert _set_synchronized_output(output, False) is True

    assert output.write_raw.call_args_list == [
        call("\x1b[?2026h"),
        call("\x1b[?2026l"),
    ]
    assert output.flush.call_count == 2


def test_scrollback_batch_moves_vt_cursor_to_next_line() -> None:
    stream = io.StringIO()
    output = Vt100_Output(
        stream,
        lambda: Size(rows=12, columns=40),
        term="xterm-256color",
        enable_cpr=False,
    )
    runtime = TuiRuntime(output_obj=output)

    runtime.viewport._print_scrollback_fragments([
        ("", "first\nsecond"),
    ])
    runtime.viewport._print_scrollback_fragments([
        ("", "third"),
    ])

    assert sanitize_terminal_text(stream.getvalue()) == (
        "first\nsecond\nthird\n"
    )


def test_transcript_screen_pairs_alternate_scroll_sequences(
    monkeypatch,
) -> None:
    output = SimpleNamespace(
        vt100_output=object(),
        write_raw=Mock(),
    )
    monkeypatch.setattr("mind_app.tui.core.screen.sys.platform", "win32")

    assert _set_alternate_scroll_mode(output, True) is True
    assert _set_alternate_scroll_mode(output, False) is True

    assert output.write_raw.call_args_list == [
        call("\x1b[?1007h"),
        call("\x1b[?1007l"),
    ]


def test_nested_synchronized_output_toggles_only_at_outer_boundary() -> None:
    runtime = TuiRuntime()

    with patch(
        "mind_app.tui.core.screen._set_synchronized_output",
        return_value=True,
    ) as set_synchronized:
        assert runtime.screen.begin_synchronized_output() is True
        assert runtime.screen.begin_synchronized_output() is True

        runtime.screen.end_synchronized_output()
        set_synchronized.assert_called_once_with(
            runtime.screen.application.output,
            True,
        )

        runtime.screen.end_synchronized_output()

    assert set_synchronized.call_args_list == [
        call(runtime.screen.application.output, True),
        call(runtime.screen.application.output, False),
    ]


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


@pytest.mark.anyio
async def test_known_inline_viewport_does_not_expand_renderer_height() -> None:
    with create_pipe_input() as pipe_input:
        output = _KnownInlineHeightOutput(
            columns=80,
            rows=18,
            available_rows=14,
        )
        runtime = TuiRuntime(input_obj=pipe_input, output_obj=output)

        await runtime.open()
        try:
            initial_screen = runtime.screen.application.renderer.last_rendered_screen
            assert initial_screen.height == 14

            runtime.set_active_renderable(
                _block("\n".join(f"line {index}" for index in range(16))),
                kind="operation",
            )
            expanded_screen = await _render_next_frame(runtime)

            assert runtime.screen.terminal_height == 14
            assert runtime.screen._visible_height() == 14
            assert expanded_screen.height == initial_screen.height
        finally:
            await runtime.close()


@pytest.mark.anyio
async def test_inline_shell_grows_known_viewport_like_stream_content() -> None:
    with create_pipe_input() as pipe_input:
        output = _KnownInlineHeightOutput(
            columns=80,
            rows=18,
            available_rows=6,
        )
        runtime = TuiRuntime(input_obj=pipe_input, output_obj=output)

        await runtime.open()
        viewer = None
        try:
            input_rows = []
            screen_heights = []

            for line_count in (1, 2, 4):
                snapshot = {
                    "ok": True,
                    "session_id": "exec_shell",
                    "command": "ping -t 8.8.8.8",
                    "status": "running",
                    "origin": "tui_shell",
                    "output_lines": [
                        f"reply {index}"
                        for index in range(line_count)
                    ],
                }
                block = exec_session_live_block(
                    snapshot,
                    terminal_width=80,
                    viewer_mode="inline",
                )
                if viewer is None:
                    viewer = runtime.begin_process_viewer(
                        ProcessViewerRequest(
                            fragments=PROCESS_VIEWER_FOCUS_REQUEST.fragments,
                            max_height=PROCESS_VIEWER_FOCUS_REQUEST.max_height,
                            capture_input=False,
                            session_id="exec_shell",
                        ),
                        block,
                    )
                else:
                    runtime.update_process_viewer(block)

                screen = await _render_next_frame(runtime)
                positions = screen.visible_windows_to_write_positions
                input_rows.append(positions[runtime.screen.input.window].ypos)
                screen_heights.append(screen.height)

                assert runtime.screen._content_input_gap_visible()

            assert input_rows == [4, 5, 7]
            assert screen_heights == [7, 8, 10]
        finally:
            if runtime.screen.process_viewer.active:
                runtime.resolve_process_viewer("detach")
                if viewer is not None:
                    await viewer
                runtime.dismiss_process_viewer()
            await runtime.close()


@pytest.mark.anyio
async def test_inline_viewport_remeasures_after_terminal_resize() -> None:
    with create_pipe_input() as pipe_input:
        output = _KnownInlineHeightOutput(
            columns=80,
            rows=18,
            available_rows=14,
        )
        runtime = TuiRuntime(input_obj=pipe_input, output_obj=output)

        await runtime.open()
        try:
            assert runtime.screen._read_frame_geometry(revision=1).height == 14

            output.size = Size(rows=24, columns=80)

            assert runtime.screen._read_frame_geometry(revision=2).height == 24
        finally:
            await runtime.close()


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
async def test_replace_transcript_replays_only_configured_tail() -> None:
    with create_pipe_input() as pipe_input:
        runtime = TuiRuntime(input_obj=pipe_input, output_obj=DummyOutput())
        runtime.configure_scrollback_reflow_line_limit(7)
        restored = tuple(
            TranscriptBlock(
                display_block=_block(f"history {index:02d}"),
                transcript_block=_block(f"history {index:02d}"),
                kind="assistant",
            )
            for index in range(20)
        )
        terminal_size = Size(rows=8, columns=40)

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
                ) as print_text:
                    runtime.replace_transcript(restored)

                    for _ in range(100):
                        await asyncio.sleep(0.002)
                        if print_text.called:
                            break

                printed = "".join(
                    text
                    for call_args in print_text.call_args_list
                    for _style, text in call_args.args[0]
                )
                visible = _document_text(runtime.document)
                main_text = f"{printed}{visible}"

                assert "history 00" not in main_text
                assert "history 19" in main_text
                assert (
                    runtime.document.stable_line_count
                    - runtime.document.cleared_line_count
                    <= 7
                )
                transcript = _transcript_text(runtime.document)
                assert "history 00" in transcript
                assert "history 19" in transcript

                with patch.object(
                    runtime.screen.application,
                    "print_text",
                ) as resize_print_text:
                    terminal_size = Size(rows=8, columns=24)
                    runtime.viewport.observe_terminal_geometry(24, 8)
                    await asyncio.sleep(0.12)

                replayed = "".join(
                    text
                    for call_args in resize_print_text.call_args_list
                    for _style, text in call_args.args[0]
                )
                resized_main_text = (
                    f"{replayed}{_document_text(runtime.document)}"
                )
                assert resize_print_text.called
                assert "history 00" not in resized_main_text
                assert "history 19" in resized_main_text
                assert len(resized_main_text.splitlines()) <= 7
            finally:
                await runtime.close()


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
async def test_scrollback_starts_synchronized_output_after_terminal_acquire() -> None:
    with create_pipe_input() as pipe_input:
        runtime = TuiRuntime(input_obj=pipe_input, output_obj=DummyOutput())
        events: list[str] = []
        printed: list[str] = []

        @asynccontextmanager
        async def controlled_terminal(render_cli_done: bool = False):
            _ = render_cli_done
            events.append("wait")
            await asyncio.sleep(0)
            events.append("acquired")
            try:
                yield
            finally:
                events.append("redraw")

        with (
            patch.object(
                runtime.screen.application.output,
                "get_size",
                return_value=Size(rows=8, columns=40),
            ),
            patch(
                "mind_app.tui.core.viewport.in_terminal",
                controlled_terminal,
            ),
        ):
            await runtime.open()
            try:
                with (
                    patch.object(
                        runtime.screen,
                        "begin_synchronized_output",
                        side_effect=lambda: events.append("begin") or True,
                    ) as begin,
                    patch.object(
                        runtime.screen,
                        "end_synchronized_output",
                        side_effect=lambda: events.append("end"),
                    ) as end,
                    patch.object(
                        runtime.screen.application,
                        "print_text",
                        side_effect=lambda value: (
                            events.append("print"),
                            printed.append(fragments_text(value)),
                        ),
                    ),
                ):
                    runtime.append_block(
                        _block("\n".join(
                            f"line {index}" for index in range(30)
                        )),
                        kind="assistant",
                    )

                    for _ in range(100):
                        await asyncio.sleep(0.002)
                        if end.called:
                            break

                begin.assert_called_once_with()
                end.assert_called_once_with()
                assert events == [
                    "wait",
                    "acquired",
                    "begin",
                    "print",
                    "redraw",
                    "end",
                ]
                assert len(printed) == 1
                assert printed[0].endswith("line 29\n")
            finally:
                await runtime.close()


@pytest.mark.anyio
async def test_scrollback_reuses_unchanged_candidate_after_terminal_acquire() -> None:
    with create_pipe_input() as pipe_input:
        runtime = TuiRuntime(input_obj=pipe_input, output_obj=DummyOutput())
        calculation_counts: list[int] = []

        with patch.object(
            runtime.screen.application.output,
            "get_size",
            return_value=Size(rows=8, columns=40),
        ):
            await runtime.open()
            try:
                with (
                    patch.object(
                        runtime.viewport,
                        "_scrollback_prefix_line_count",
                        wraps=runtime.viewport._scrollback_prefix_line_count,
                    ) as calculate,
                    patch.object(
                        runtime.screen.application,
                        "print_text",
                        side_effect=lambda _value: calculation_counts.append(
                            calculate.call_count
                        ),
                    ) as print_text,
                ):
                    runtime.append_block(
                        _block("\n".join(
                            f"line {index}" for index in range(30)
                        )),
                        kind="assistant",
                    )

                    for _ in range(100):
                        await asyncio.sleep(0.002)
                        if print_text.called:
                            break

                assert calculation_counts == [1]
                assert runtime.document.scrollback_line_count > 0
            finally:
                await runtime.close()


@pytest.mark.anyio
async def test_scrollback_rechecks_submitted_query_before_printing() -> None:
    with create_pipe_input() as pipe_input:
        runtime = TuiRuntime(input_obj=pipe_input, output_obj=DummyOutput())
        printed: list[str] = []
        query = _block("\n".join(f"query {index}" for index in range(20)))

        @asynccontextmanager
        async def mark_query_while_waiting(render_cli_done: bool = False):
            _ = render_cli_done
            runtime.viewport.mark_submitted_query(query)
            yield

        with patch.object(
            runtime.screen.application.output,
            "get_size",
            return_value=Size(rows=8, columns=40),
        ):
            await runtime.open()
            try:
                runtime.set_execution_active(True)
                runtime.append_block(
                    _block("\n".join(
                        f"prior {index}" for index in range(30)
                    )),
                    kind="assistant",
                )
                runtime.append_block(query, kind="user")

                with (
                    patch(
                        "mind_app.tui.core.viewport.in_terminal",
                        mark_query_while_waiting,
                    ),
                    patch.object(
                        runtime.screen.application,
                        "print_text",
                        side_effect=lambda value: printed.append(
                            fragments_text(value)
                        ),
                    ) as print_text,
                ):
                    runtime.set_execution_active(False)

                    for _ in range(100):
                        await asyncio.sleep(0.002)
                        if print_text.called:
                            break

                assert len(printed) == 1
                assert "prior 29" in printed[0]
                assert "query 0" not in printed[0]
                visible = _document_text(runtime.document)
                assert "prior 29" not in visible
                assert "query 0" in visible
            finally:
                runtime.set_execution_active(False)
                await runtime.close()


@pytest.mark.anyio
async def test_scrollback_discards_candidate_after_unobserved_resize() -> None:
    with create_pipe_input() as pipe_input:
        runtime = TuiRuntime(input_obj=pipe_input, output_obj=DummyOutput())
        terminal_size = {"value": Size(rows=8, columns=40)}

        @asynccontextmanager
        async def resize_while_waiting(render_cli_done: bool = False):
            _ = render_cli_done
            terminal_size["value"] = Size(rows=8, columns=24)
            yield

        with patch.object(
            runtime.screen.application.output,
            "get_size",
            side_effect=lambda: terminal_size["value"],
        ):
            await runtime.open()
            try:
                with (
                    patch(
                        "mind_app.tui.core.viewport.in_terminal",
                        resize_while_waiting,
                    ),
                    patch.object(
                        runtime.viewport,
                        "_schedule_scrollback_reflow",
                    ) as schedule_reflow,
                    patch.object(
                        runtime.screen.application,
                        "print_text",
                    ) as print_text,
                ):
                    runtime.append_block(
                        _block("\n".join(
                            f"line {index}" for index in range(30)
                        )),
                        kind="assistant",
                    )

                    for _ in range(100):
                        await asyncio.sleep(0.002)
                        if runtime.viewport.scrollback_task is None:
                            break

                print_text.assert_not_called()
                schedule_reflow.assert_any_call()
                assert runtime.document.scrollback_line_count == 0
                assert runtime.viewport._observed_geometry == (24, 8)
                assert runtime.viewport._reflow_required is True
            finally:
                runtime.viewport._reflow_required = False
                await runtime.close()


@pytest.mark.anyio
async def test_assistant_stream_flushes_to_scrollback_after_commit() -> None:
    with create_pipe_input() as pipe_input:
        runtime = TuiRuntime(input_obj=pipe_input, output_obj=DummyOutput())
        output = TuiOutputControl("", runtime=runtime, animate=False)
        source = "\n".join(f"line {index:02d}" for index in range(80))

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
                    await _render_next_frame(runtime)
                    await asyncio.sleep(0.12)

                    assert not print_text.called
                    assert runtime.document.scrollback_line_count == 0

                    await output.prepare_external_output()
                    runtime.set_execution_active(False)

                    for _ in range(100):
                        await asyncio.sleep(0.002)
                        if runtime.document.scrollback_line_count > 0:
                            break

                printed = "\n".join(
                    "".join(text for _style, text in call.args[0])
                    for call in print_text.call_args_list
                )
                visible = _document_text(runtime.document)
                expected = "\n".join([
                    "• line 00",
                    *(f"  line {index:02d}" for index in range(1, 80)),
                ])

                assert print_text.called
                assert runtime.document.scrollback_line_count > 0
                assert printed == expected + "\n"
                assert visible == ""
            finally:
                runtime.set_execution_active(False)
                await runtime.close()


@pytest.mark.anyio
async def test_active_stream_does_not_schedule_native_scrollback() -> None:
    with create_pipe_input() as pipe_input:
        runtime = TuiRuntime(input_obj=pipe_input, output_obj=DummyOutput())
        output = TuiOutputControl("", runtime=runtime, animate=False)
        events: list[str] = []

        @asynccontextmanager
        async def controlled_terminal(render_cli_done: bool = False):
            _ = render_cli_done
            events.append("terminal enter")
            try:
                yield
            finally:
                events.append("terminal exit")

        with (
            patch.object(
                runtime.screen.application.output,
                "get_size",
                return_value=Size(rows=12, columns=40),
            ),
            patch(
                "mind_app.tui.core.viewport.in_terminal",
                controlled_terminal,
            ),
        ):
            await runtime.open()
            try:
                with (
                    patch.object(
                        runtime.screen,
                        "begin_synchronized_output",
                        side_effect=(
                            lambda: events.append("sync begin") or True
                        ),
                    ) as begin,
                    patch.object(
                        runtime.screen,
                        "end_synchronized_output",
                        side_effect=lambda: events.append("sync end"),
                    ) as end,
                    patch.object(
                        runtime.screen.application,
                        "print_text",
                        side_effect=lambda _value: events.append("print"),
                    ) as print_text,
                ):
                    runtime.set_execution_active(True)
                    await output.append_assistant_delta("\n".join(
                        f"line {index:02d}" for index in range(80)
                    ))
                    await _render_next_frame(runtime)
                    await asyncio.sleep(0.12)

                begin.assert_not_called()
                end.assert_not_called()
                print_text.assert_not_called()
                assert not events
                assert runtime.viewport.scrollback_task is None
                assert runtime.viewport._stream_scrollback_handle is None
                assert runtime.document.scrollback_line_count == 0
            finally:
                runtime.set_execution_active(False)
                await runtime.close()


@pytest.mark.anyio
async def test_continuous_markdown_stream_renders_only_complete_source_lines() -> None:
    with create_pipe_input() as pipe_input:
        runtime = TuiRuntime(input_obj=pipe_input, output_obj=DummyOutput())
        output = TuiOutputControl("", runtime=runtime, animate=False)

        with patch.object(
            runtime.screen.application.output,
            "get_size",
            return_value=Size(rows=12, columns=40),
        ):
            await runtime.open()
            try:
                with patch(
                        "mind_app.tui.adapters.markdown.render_tui_markdown",
                    wraps=render_tui_markdown,
                ) as render:
                    runtime.set_execution_active(True)
                    for index in range(40):
                        prefix = "" if index == 0 else "\n"
                        await output.append_assistant_delta(
                            prefix
                            + f"- **entry {index:02d}** "
                            + "[docs](https://example.com/reference)"
                        )
                        await asyncio.sleep(0.005)

                    await asyncio.sleep(0.12)

                    render.assert_not_called()
                    active = runtime.document.active_block
                    assert active is not None
                    active_text = fragments_text(active.fragments)
                    assert "**" not in active_text
                    assert "https://" not in active_text
                    assert "entry 00 docs" in active_text
                    assert "entry 38 docs" in active_text
                    assert "entry 39 docs" not in active_text
                    assert any(
                        "bold" in style
                        for style, text in active.fragments
                        if text.strip()
                    )

                    await output.settle_stream()

                    render.assert_not_called()
                    active = runtime.document.active_block
                    assert active is not None
                    assert "entry 39 docs" in fragments_text(active.fragments)

                    source = output.assistant.text
                    await output.prepare_external_output()

                    render.assert_called_once_with(
                        source,
                        hyperlinks=runtime.hyperlinks_enabled,
                        width=max(1, runtime.terminal_width - 2),
                    )
                    settled = runtime.document.blocks[-1].display_block
                    settled_text = fragments_text(settled.fragments)
                    assert "**" not in settled_text
                    assert "https://" not in settled_text
                    assert any(
                        "bold" in style
                        for style, text in settled.fragments
                        if text.strip()
                    )
            finally:
                runtime.set_execution_active(False)
                await runtime.close()


def test_markdown_uses_terminal_native_semantic_hierarchy() -> None:
    block = render_tui_markdown(
        "# Primary\n\n"
        "## Secondary\n\n"
        "### Tertiary\n\n"
        "#### Detail\n\n"
        "Body with **strong**, *emphasis*, ~~old~~ and `code`.\n\n"
        "- bullet\n"
        "1. ordered\n\n"
        "> quoted\n\n"
        "---\n\n"
        "[docs](https://example.com)",
        width=60,
    )
    fragments = list(block.fragments)
    text = fragments_text(fragments)

    assert text.startswith(
        "# Primary\n\n## Secondary\n\n### Tertiary\n\n#### Detail"
    )
    assert any(
        "bold" in style and "underline" in style and value == "# Primary"
        for style, value in fragments
    )
    assert any(
        "bold" in style and "italic" in style and value == "### Tertiary"
        for style, value in fragments
    )
    assert any(
        "dim" in style and "italic" in style and value == "#### Detail"
        for style, value in fragments
    )
    assert any("strike" in style and value == "old" for style, value in fragments)
    assert any("fg:" in style and value == "code" for style, value in fragments)
    assert any("dim" in style and value == "- " for style, value in fragments)
    assert any("fg:" in style and value == "1. " for style, value in fragments)
    assert any("fg:" in style and value == "▎ " for style, value in fragments)
    assert any("dim" in style and value == "quoted" for style, value in fragments)
    assert "———" in text
    assert any(
        "underline" in style and value == "docs"
        for style, value in fragments
    )
    assert any(style == "" and "Body with " in value for style, value in fragments)


def test_multiline_list_items_align_wrapped_content_and_stay_separated() -> None:
    block = render_tui_markdown(
        "1. a deliberately long first list item for wrapping\n"
        "2. short second item",
        width=24,
    )
    lines = fragments_text(block.fragments).splitlines()

    assert lines[0].startswith("1. ")
    assert lines[1].startswith("   ")
    assert lines[2].startswith("   ")
    assert "" in lines
    assert lines[-1].startswith("2. ")
    assert all(get_cwidth(line) <= 24 for line in lines)


def test_markdown_stream_replaces_only_the_mutable_setext_tail() -> None:
    renderer = TuiMarkdownStreamRenderer()

    paragraph = renderer.render("first\n\nTitle\n")
    assert fragments_text(paragraph.fragments) == "first\n\nTitle"
    assert all(
        "bold" not in style
        for style, text in paragraph.fragments
        if text == "Title"
    )

    heading = renderer.render("first\n\nTitle\n---\n")
    assert fragments_text(heading.fragments) == "first\n\n## Title"
    assert any(
        "bold" in style
        for style, text in heading.fragments
        if text == "## Title"
    )


@pytest.mark.anyio
async def test_split_markdown_tokens_appear_only_after_the_source_line_closes() -> None:
    runtime = TuiRuntime()
    output = TuiOutputControl("", runtime=runtime, animate=False)

    await output.append_assistant_delta("**bo")
    await output.append_assistant_delta("ld**")
    assert runtime.document.active_block is None

    await output.append_assistant_delta("\n")
    active = runtime.document.active_block
    assert active is not None
    assert fragments_text(active.fragments) == "• bold"
    assert any(
        "bold" in style
        for style, text in active.fragments
        if text == "bold"
    )


def test_markdown_stream_does_not_rerender_stable_top_level_blocks() -> None:
    renderer = TuiMarkdownStreamRenderer()

    with patch.object(
        tui_markdown._MARKDOWN,
        "parse",
        wraps=tui_markdown._MARKDOWN.parse,
    ) as parse:
        renderer.render("first\n\nsecond\n")
        first_parse_count = parse.call_count
        renderer.render("first\n\nsecond\n\nthird\n")

    new_sources = [
        call_args.args[0]
        for call_args in parse.call_args_list[first_parse_count:]
    ]
    assert new_sources == ["second\n\nthird\n"]


@pytest.mark.parametrize(
    ("initial", "extended"),
    (
        ("- first\n\n", "- first\n\n  continuation\n\n"),
        ("1. first\n\n", "1. first\n\n   second paragraph\n\n"),
    ),
)
def test_markdown_stream_keeps_the_last_commonmark_block_mutable(
    initial: str,
    extended: str,
) -> None:
    renderer = TuiMarkdownStreamRenderer()
    renderer.render(initial, width=40)

    streamed = renderer.render(extended, width=40)
    final = render_tui_markdown(extended, width=40)

    assert fragments_text(streamed.fragments) == fragments_text(final.fragments)


def test_plain_multiline_stream_skips_repeated_markdown_parsing() -> None:
    renderer = TuiMarkdownStreamRenderer()
    source = ""

    with patch.object(tui_markdown._MARKDOWN, "parse") as parse:
        for index in range(1000):
            source += f"plain line {index}\n"
            renderer.render(source, width=80)

    parse.assert_not_called()


def test_reference_link_definition_recomputes_the_stable_prefix() -> None:
    renderer = TuiMarkdownStreamRenderer()
    unresolved = renderer.render(
        "[docs][ref]\n\nfollowing paragraph\n",
        width=40,
    )
    assert "[docs][ref]" in fragments_text(unresolved.fragments)

    resolved = renderer.render(
        "[docs][ref]\n\nfollowing paragraph\n\n"
        "[ref]: https://example.com/reference\n",
        width=40,
    )

    assert "[docs][ref]" not in fragments_text(resolved.fragments)
    assert fragments_text(resolved.fragments).startswith("docs\n\n")
    assert any(
        "underline" in style
        for style, text in resolved.fragments
        if text == "docs"
    )


@pytest.mark.anyio
async def test_streaming_fence_never_displays_raw_fence_markers() -> None:
    runtime = TuiRuntime()
    output = TuiOutputControl("", runtime=runtime, animate=False)

    await output.append_assistant_delta("```python\nprint('ok')\n")

    active = runtime.document.active_block
    assert active is not None
    assert fragments_text(active.fragments) == "• print('ok')"
    assert any(
        "fg:" in style
        for style, text in active.fragments
        if "print" in text
    )

    await output.append_assistant_delta("```\n")
    assert "```" not in _document_text(runtime.document)


@pytest.mark.anyio
async def test_streaming_table_replaces_the_whole_mutable_tail() -> None:
    runtime = TuiRuntime()
    output = TuiOutputControl("", runtime=runtime, animate=False)

    await output.append_assistant_delta("| Name | Value |\n")
    assert "| Name | Value |" in _document_text(runtime.document)

    await output.append_assistant_delta("|:---|---:|\n")
    header = _document_text(runtime.document)
    assert "|:---|---:|" not in header
    assert "━" in header
    assert not any(character in header for character in "┌┬┐├┼┤└┴┘│")

    await output.append_assistant_delta("| Longer Name | 1000 |\n")
    table = _document_text(runtime.document)
    assert "Longer Name" in table
    assert table.count("━") > header.count("━")
    assert any(
        "bold" in style
        for style, text in runtime.document.active_block.fragments
        if text in {"Name", "Value"}
    )


def test_streaming_table_wraps_cells_without_heavy_borders() -> None:
    renderer = TuiMarkdownStreamRenderer()
    block = renderer.render(
        "| Name | Value |\n"
        "|---|---|\n"
        "| a very long name | a very long value |\n",
        width=22,
    )
    lines = fragments_text(block.fragments).splitlines()

    assert len(lines) >= 4
    assert all(get_cwidth(line) == 22 for line in lines)
    assert set(lines[1]) == {"━"}
    assert not any(
        character in "".join(lines)
        for character in "┌┬┐├┼┤└┴┘│"
    )


def test_narrow_markdown_table_switches_to_stacked_records() -> None:
    block = render_tui_markdown(
        "| Name | Status | Description |\n"
        "|---|---|---|\n"
        "| API | Ready | Service is available |\n"
        "| Worker | Running | Processing jobs |\n",
        width=22,
    )
    lines = fragments_text(block.fragments).splitlines()

    assert lines[:6] == [
        " Name",
        "  API",
        " Status",
        "  Ready",
        " Description",
        "  Service is available",
    ]
    assert set(lines[6]) == {"─"}
    assert "  Processing jobs" in lines
    assert all(get_cwidth(line) <= 22 for line in lines)


@pytest.mark.parametrize("width", (22, 80))
def test_streaming_table_increment_matches_full_render(width: int) -> None:
    renderer = TuiMarkdownStreamRenderer()
    source = "| Name | Value |\n|---|---|\n"
    rows = (
        "| A | one |\n",
        "| Longer Name | `two` |\n",
        "| C | a value that changes the fitted width |\n",
    )

    for row in rows:
        source += row
        assert renderer.render(source, width=width) == render_tui_markdown(
            source,
            width=width,
        )

    source += "\n| separate | paragraph |\n"
    assert renderer.render(source, width=width) == render_tui_markdown(
        source,
        width=width,
    )


def test_streaming_table_parses_only_the_appended_row() -> None:
    renderer = TuiMarkdownStreamRenderer()
    source = "| Name | Value |\n|---|---|\n| existing | one |\n"
    renderer.render(source, width=80)

    with patch.object(
        tui_markdown._MARKDOWN,
        "parse",
        wraps=tui_markdown._MARKDOWN.parse,
    ) as parse:
        renderer.render(source + "| appended | two |\n", width=80)

    parse.assert_called_once()
    parsed_source = parse.call_args.args[0]
    assert "appended" in parsed_source
    assert "existing" not in parsed_source


@pytest.mark.anyio
async def test_source_reflow_invalidates_the_screen_transcript_cache() -> None:
    runtime = TuiRuntime()
    size = [60, 24]
    runtime.screen._output_size = lambda: tuple(size)
    output = TuiOutputControl("", runtime=runtime, animate=False)
    source = (
        "| Name | Status | Description |\n"
        "|---|---|---|\n"
        "| API | Ready | Service is available |\n"
        "| Worker | Running | Processing jobs |\n"
    )

    await output.append_assistant_delta(source)
    await output.prepare_external_output()
    runtime.screen.transcript_fragments()

    size[0] = 22
    runtime.document.set_display_width(22, reflow_sources=False)
    before_reflow = fragments_text(runtime.screen.transcript_fragments())

    assert runtime.document.set_display_width(22, reflow_sources=True)
    after_reflow = fragments_text(runtime.screen.transcript_fragments())
    expected = fragments_text(
        runtime.document.fragments(width=22, reflow_sources=False)
    )

    assert after_reflow != before_reflow
    assert after_reflow == expected
    assert after_reflow.count("Description") == 2
    assert all(get_cwidth(line) <= 22 for line in after_reflow.splitlines())


def test_markdown_only_table_fence_is_rendered_as_a_table() -> None:
    source = (
        "```markdown\n"
        "| Name | Value |\n"
        "|---|---|\n"
        "| API | Ready |\n"
        "```"
    )
    block = render_tui_markdown(source, width=30)
    text = fragments_text(block.fragments)

    assert "```" not in text
    assert "|---|" not in text
    assert "━" in text
    assert "API" in text and "Ready" in text


def test_markdown_table_fence_accepts_a_longer_closing_fence() -> None:
    block = render_tui_markdown(
        "```markdown\n"
        "| Name | Value |\n"
        "|---|---|\n"
        "| API | Ready |\n"
        "````",
        width=30,
    )
    text = fragments_text(block.fragments)

    assert "|---|" not in text
    assert "━" in text


@pytest.mark.anyio
@pytest.mark.parametrize("animate", (False, True))
async def test_streamed_tool_table_never_leaves_a_duplicate_mutable_row(
    animate: bool,
) -> None:
    runtime = TuiRuntime()
    runtime.screen._output_size = lambda: (48, 24)
    output = TuiOutputControl("", runtime=runtime, animate=animate)
    source = (
        "## 命令行工具\n\n"
        "| 工具 | 用途 |\n"
        "|---|---|\n"
        "| rg (ripgrep) | 快速文本搜索、文件发现 |\n"
        "| ast-grep | 基于语法结构的代码搜索和改写 |\n"
        "| jq | JSON 数据处理和查询 |\n"
        "| yq | YAML 数据处理和查询 |\n"
        "| sqlite3 | SQLite 数据库查询 |\n"
        "| xh | HTTP 请求（类似 httpie） |\n"
        "| 7z | 文件压缩/解压 |\n"
    )

    for line in source.splitlines(keepends=True):
        await output.append_assistant_delta(line)
        if animate:
            output._cancel_stream_render()
            while output._stream_visible_rows < len(output._stream_rows):
                output._render_stream_frame()

        active = runtime.document.active_block
        visible = fragments_text(active.fragments) if active is not None else ""
        assert visible.count("xh") <= 1
        if "xh" in visible:
            assert visible.index("## 命令行工具") < visible.index("xh")

    streamed = fragments_text(runtime.document.active_block.fragments)
    await output.prepare_external_output()
    final = fragments_text(runtime.document.blocks[-1].display_block.fragments)

    assert streamed.count("xh") == 1
    assert final.count("xh") == 1
    assert streamed == final


def test_non_table_markdown_fence_stays_a_code_block() -> None:
    block = render_tui_markdown(
        "```markdown\n# literal heading\n```",
        width=30,
    )

    assert fragments_text(block.fragments) == "# literal heading"
    assert all(
        "bold" not in style
        for style, text in block.fragments
        if "literal heading" in text
    )


@pytest.mark.anyio
async def test_final_markdown_table_rerenders_from_source_on_resize() -> None:
    runtime = TuiRuntime()
    output = TuiOutputControl("", runtime=runtime, animate=False)
    source = (
        "| Name | Value |\n"
        "|---|---|\n"
        "| a very long name | a very long value |"
    )

    await output.append_assistant_delta(source)
    await output.prepare_external_output()

    narrow = fragments_text(runtime.document.fragments(width=24)).splitlines()
    assert all(get_cwidth(line) == 24 for line in narrow)
    assert set(narrow[1].strip()) == {"━"}
    assert not any(
        character in "".join(narrow)
        for character in "┌┬┐├┼┤└┴┘│"
    )
    cell = runtime.document.blocks[-1]
    transcript = fragments_text(
        runtime.document.transcript_cell_fragments(cell, width=24)
    ).splitlines()
    assert transcript == narrow
    assert cell.raw_text == source


@pytest.mark.anyio
async def test_stream_animation_releases_one_complete_display_row_per_tick() -> None:
    runtime = TuiRuntime()
    output = TuiOutputControl("", runtime=runtime, animate=True)

    await output.append_assistant_delta("one\ntwo\nthree\n")
    output._cancel_stream_render()

    assert output._stream_visible_rows == 1
    assert _document_text(runtime.document) == "• one"

    output._render_stream_frame()
    assert output._stream_visible_rows == 2
    assert _document_text(runtime.document) == "• one\n  two"

    output._render_stream_frame()
    assert output._stream_visible_rows == 3
    assert _document_text(runtime.document) == "• one\n  two\n  three"


@pytest.mark.anyio
async def test_stream_animation_catches_up_when_line_queue_is_large() -> None:
    runtime = TuiRuntime()
    output = TuiOutputControl("", runtime=runtime, animate=True)
    source = "".join(f"line {index}\n" for index in range(8))

    await output.append_assistant_delta(source)

    assert output._stream_visible_rows == len(output._stream_rows) == 8
    assert _document_text(runtime.document).endswith("  line 7")


@pytest.mark.anyio
async def test_stream_animation_catches_up_when_oldest_row_waits_too_long() -> None:
    runtime = TuiRuntime()
    output = TuiOutputControl("", runtime=runtime, animate=True)

    await output.append_assistant_delta("one\ntwo\nthree\n")
    output._cancel_stream_render()
    assert output._stream_visible_rows == 1
    assert output._stream_oldest_pending_at is not None

    output._stream_oldest_pending_at -= 0.13
    output._render_stream_frame()

    assert output._stream_visible_rows == len(output._stream_rows) == 3
    assert _document_text(runtime.document).endswith("  three")


@pytest.mark.anyio
async def test_active_markdown_stream_rerenders_from_source_on_resize() -> None:
    with create_pipe_input() as pipe_input:
        runtime = TuiRuntime(input_obj=pipe_input, output_obj=DummyOutput())
        output = TuiOutputControl("", runtime=runtime, animate=False)
        terminal_size = Size(rows=24, columns=40)
        source = "x" * 70

        with patch.object(
            runtime.screen.application.output,
            "get_size",
            side_effect=lambda: terminal_size,
        ):
            await runtime.open()
            try:
                with patch.object(
                    output._markdown_stream,
                    "render",
                    wraps=output._markdown_stream.render,
                ) as render:
                    await output.append_assistant_delta(source + "\n")
                    await _render_next_frame(runtime)
                    assert output._stream_width == 38

                    terminal_size = Size(rows=24, columns=24)
                    await _render_next_frame(runtime)
                    await asyncio.sleep(0.09)
                    await _render_next_frame(runtime)

                assert output._stream_width == 22
                assert render.call_count == 2
                lines = _document_text(runtime.document).splitlines()
                assert "".join(line[2:] for line in lines) == source
            finally:
                await runtime.close()


@pytest.mark.anyio
async def test_scrollback_commits_oversized_replies_as_complete_blocks() -> None:
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
                assert _document_text(runtime.document) == "\nFinished first"
                assert "first 29" in fragments_text(
                    runtime.document.all_fragments(width=40)
                )

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
                assert "second 29" not in visible
                assert visible == "\nFinished second"
                assert "second 29" in fragments_text(
                    runtime.document.all_fragments(width=40)
                )
            finally:
                await runtime.close()


@pytest.mark.anyio
async def test_single_oversized_block_retires_as_one_complete_block() -> None:
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
                assert printed.endswith("  line 79\n")
                assert printed == expected + "\n"
                assert visible == ""
                assert (
                    runtime.document.scrollback_line_count
                    == runtime.document.stable_line_count
                )
            finally:
                await runtime.close()


@pytest.mark.anyio
async def test_completed_scrollback_collapses_retired_canvas_height() -> None:
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

                for _ in range(100):
                    await asyncio.sleep(0.002)
                    if runtime.document.scrollback_line_count > 0:
                        break

                screen = await _render_next_frame(runtime)
                positions = screen.visible_windows_to_write_positions

                assert runtime.document.scrollback_line_count > 0
                assert runtime.screen._canvas_height_floor == (
                    runtime.screen._natural_visible_height()
                )
                assert runtime.screen.canvas_spacer not in positions
            finally:
                await runtime.close()


@pytest.mark.anyio
async def test_complete_block_scrollback_resize_never_reprints_content() -> None:
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
                    assert first_count == runtime.document.stable_line_count

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

                assert chunks == [source + "\n"]
                assert visible == ""
                assert runtime.document.scrollback_line_count == first_count
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
                    "clear_terminal_for_resize_replay",
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
                assert chunks == [source + "\n"]
                assert visible == ""
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
                    "clear_terminal_for_resize_replay",
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
                    "clear_terminal_for_resize_replay",
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
                visible = _document_text(runtime.document)
                assert chunks == [source + "\n"]
                assert visible == ""
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
                    "clear_terminal_for_resize_replay",
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
async def test_resize_storm_replays_once_in_synchronized_output() -> None:
    with create_pipe_input() as pipe_input:
        runtime = TuiRuntime(input_obj=pipe_input, output_obj=DummyOutput())
        terminal_size = Size(rows=8, columns=40)
        events: list[str] = []

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

                @asynccontextmanager
                async def controlled_terminal(
                    render_cli_done: bool = False,
                ):
                    _ = render_cli_done
                    events.append("wait")
                    await asyncio.sleep(0)
                    events.append("acquired")
                    try:
                        yield
                    finally:
                        events.append("redraw")

                with (
                    patch(
                        "mind_app.tui.core.viewport.in_terminal",
                        controlled_terminal,
                    ),
                    patch.object(
                        runtime.screen,
                        "begin_synchronized_output",
                        side_effect=lambda: events.append("begin") or True,
                    ) as begin,
                    patch.object(
                        runtime.screen,
                        "clear_terminal_for_resize_replay",
                        side_effect=lambda: events.append("clear"),
                    ) as clear,
                    patch.object(
                        runtime.screen,
                        "end_synchronized_output",
                        side_effect=lambda: events.append("end"),
                    ) as end,
                    patch.object(
                        runtime.screen.application,
                        "print_text",
                        side_effect=lambda _fragments: events.append("replay"),
                    ),
                    patch.object(
                        runtime.screen,
                        "settle_scrollback_layout",
                        side_effect=lambda: events.append("settle"),
                    ) as settle,
                    patch.object(
                        runtime.screen.application.renderer,
                        "clear",
                    ) as renderer_clear,
                ):
                    for width, height in (
                        (30, 9),
                        (22, 11),
                        (48, 14),
                        (36, 12),
                    ):
                        terminal_size = Size(rows=height, columns=width)
                        runtime.viewport.observe_terminal_geometry(width, height)

                    await asyncio.sleep(0.12)

                begin.assert_called_once_with()
                clear.assert_called_once_with()
                settle.assert_called_once_with()
                end.assert_called_once_with()
                renderer_clear.assert_not_called()
                assert events == [
                    "wait",
                    "acquired",
                    "begin",
                    "clear",
                    "replay",
                    "settle",
                    "redraw",
                    "end",
                ]
                assert runtime.viewport._reflowed_geometry == (36, 12)
            finally:
                await runtime.close()


@pytest.mark.anyio
async def test_resize_invalidated_while_acquiring_terminal_keeps_position() -> None:
    with create_pipe_input() as pipe_input:
        runtime = TuiRuntime(input_obj=pipe_input, output_obj=DummyOutput())
        terminal_size = {"value": Size(rows=8, columns=40)}

        with patch.object(
            runtime.screen.application.output,
            "get_size",
            side_effect=lambda: terminal_size["value"],
        ):
            await runtime.open()
            try:
                runtime.append_block(
                    _block("\n".join(f"entry {index}" for index in range(60))),
                    kind="assistant",
                )
                await asyncio.sleep(0.02)

                previous_position = runtime.document.scrollback_line_count
                assert previous_position > 0

                terminal_size["value"] = Size(rows=8, columns=24)
                runtime.viewport.observe_terminal_geometry(24, 8)
                runtime.viewport._cancel_scrollback_reflow()
                generation = runtime.viewport._reflow_generation

                @asynccontextmanager
                async def resize_while_waiting(
                    render_cli_done: bool = False,
                ):
                    _ = render_cli_done
                    terminal_size["value"] = Size(rows=8, columns=40)
                    runtime.viewport.observe_terminal_geometry(40, 8)
                    yield

                with (
                    patch(
                        "mind_app.tui.core.viewport.in_terminal",
                        resize_while_waiting,
                    ),
                    patch.object(
                        runtime.screen,
                        "clear_terminal_for_resize_replay",
                    ) as clear,
                ):
                    await runtime.viewport._reflow_scrollback(
                        (24, 8),
                        generation,
                    )

                clear.assert_not_called()
                assert runtime.document.scrollback_line_count == previous_position
                assert runtime.viewport._observed_geometry == (40, 8)
                assert runtime.viewport._reflowed_geometry == (40, 8)
                assert runtime.viewport._reflow_required is False
            finally:
                await runtime.close()


@pytest.mark.anyio
async def test_resize_replay_failure_restores_scrollback_position() -> None:
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

                previous_position = runtime.document.scrollback_line_count
                terminal_size = Size(rows=10, columns=24)
                runtime.viewport.observe_terminal_geometry(24, 10)
                runtime.viewport._cancel_scrollback_reflow()
                generation = runtime.viewport._reflow_generation

                @asynccontextmanager
                async def controlled_terminal(
                    render_cli_done: bool = False,
                ):
                    _ = render_cli_done
                    yield

                with (
                    patch(
                        "mind_app.tui.core.viewport.in_terminal",
                        controlled_terminal,
                    ),
                    patch.object(
                        runtime.screen,
                        "begin_synchronized_output",
                        return_value=True,
                    ),
                    patch.object(
                        runtime.screen,
                        "end_synchronized_output",
                    ) as end,
                    patch.object(
                        runtime.screen.application,
                        "print_text",
                        side_effect=RuntimeError("replay failed"),
                    ),
                ):
                    with pytest.raises(RuntimeError, match="replay failed"):
                        await runtime.viewport._reflow_scrollback(
                            (24, 10),
                            generation,
                        )

                end.assert_called_once_with()
                assert runtime.document.scrollback_line_count == previous_position
                assert runtime.viewport._reflow_required is True
            finally:
                runtime.viewport._cancel_scrollback_reflow()
                await runtime.close()


@pytest.mark.anyio
async def test_resize_rechecks_geometry_reported_after_first_replay() -> None:
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
                    "clear_terminal_for_resize_replay",
                ) as clear:
                    terminal_size = Size(rows=8, columns=24)
                    runtime.viewport.observe_terminal_geometry(24, 8)
                    await asyncio.sleep(0.11)
                    clear.assert_called_once_with()

                    terminal_size = Size(rows=12, columns=30)
                    await asyncio.sleep(0.2)

                assert clear.call_count == 2
                assert runtime.viewport._observed_geometry == (30, 12)
                assert runtime.viewport._reflowed_geometry == (30, 12)
            finally:
                await runtime.close()


@pytest.mark.anyio
async def test_resize_during_stream_replays_final_stable_content() -> None:
    with create_pipe_input() as pipe_input:
        runtime = TuiRuntime(input_obj=pipe_input, output_obj=DummyOutput())
        terminal_size = Size(rows=8, columns=40)
        stable_source = "\n".join(
            f"stable entry {index:02d}" for index in range(60)
        )
        stream_source = "\n".join(
            f"stream entry {index:02d}" for index in range(30)
        )

        with patch.object(
            runtime.screen.application.output,
            "get_size",
            side_effect=lambda: terminal_size,
        ):
            await runtime.open()
            try:
                runtime.append_block(_block(stable_source), kind="assistant")
                await asyncio.sleep(0.02)
                runtime.set_active_renderable(
                    _block(stream_source),
                    kind="assistant",
                    raw_text=stream_source,
                    stream_continuation=True,
                )

                with patch.object(
                    runtime.screen,
                    "clear_terminal_for_resize_replay",
                ) as clear:
                    terminal_size = Size(rows=10, columns=24)
                    runtime.viewport.observe_terminal_geometry(24, 10)
                    await asyncio.sleep(0.12)

                    clear.assert_called_once_with()
                    assert runtime.viewport._resize_during_stream is True
                    await _render_next_frame(runtime)
                    assert runtime.screen._canvas_height_floor == 10

                    runtime.commit_active_renderable(
                        _block(stream_source),
                        raw_text=stream_source,
                    )
                    await asyncio.sleep(0.05)

                assert clear.call_count == 2
                assert runtime.viewport._resize_during_stream is False
                assert runtime.viewport._reflowed_geometry == (24, 10)
                transcript = _transcript_text(runtime.document)
                assert transcript.count("stream entry 00") == 1
                assert transcript.count("stream entry 29") == 1
                screen = await _render_next_frame(runtime)
                assert runtime.screen._canvas_height_floor == (
                    runtime.screen._natural_visible_height()
                )
                assert runtime.screen.canvas_spacer not in (
                    screen.visible_windows_to_write_positions
                )
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
                    "clear_terminal_for_resize_replay",
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
    document.commit_scrollback_prefix(20, expected_start=0)
    document.clear_visible_prefix()
    document.append_block(
        _block("\n".join(f"new {index}" for index in range(20))),
        kind="assistant",
    )
    document.commit_scrollback_prefix(21, expected_start=21)

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
async def test_activity_height_reduction_keeps_slack_above_transcript() -> None:
    with create_pipe_input() as pipe_input:
        runtime = TuiRuntime(input_obj=pipe_input, output_obj=DummyOutput())

        with patch.object(
            runtime.screen.application.output,
            "get_size",
            return_value=Size(rows=24, columns=40),
        ):
            await runtime.open()
            try:
                runtime.append_block(_block("answer"), kind="assistant")
                runtime.screen.set_activity_renderable(_block(
                    "first status\nsecond status\nthird status"
                ))
                screen = await _render_next_frame(runtime)
                position = screen.visible_windows_to_write_positions[
                    runtime.screen.input.window
                ]
                input_row = (
                    24 - runtime.screen._visible_height() + position.ypos
                )

                runtime.screen.clear_activity_renderable()
                screen = await _render_next_frame(runtime)
                positions = screen.visible_windows_to_write_positions
                position = positions[runtime.screen.input.window]
                spacer = positions[runtime.screen.canvas_spacer]
                transcript = positions[runtime.screen.transcript_window]
                content_gap = positions[
                    runtime.screen.content_input_gap.content
                ]

                assert (
                    24 - runtime.screen._visible_height() + position.ypos == input_row
                )
                assert spacer.ypos + spacer.height == transcript.ypos
                assert content_gap.ypos == (
                    transcript.ypos + transcript.height
                )
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
async def test_shell_completion_keeps_baseline_layout_and_footer() -> None:
    with create_pipe_input() as pipe_input:
        runtime = TuiRuntime(input_obj=pipe_input, output_obj=DummyOutput())

        with patch.object(
            runtime.screen.application.output,
            "get_size",
            return_value=Size(rows=12, columns=80),
        ):
            await runtime.open()
            try:
                runtime.append_block(_block(">_ App (v1.0)"), kind="system")
                runtime.append_block(
                    exec_session_summary_block({
                        "ok": True,
                        "command": "adb devices",
                        "status": "exited",
                        "origin": "tui_shell",
                        "output_lines": ["List of devices attached"],
                    }, terminal_width=80),
                    kind="notice",
                )

                screen = await _render_next_frame(runtime)
                rows = {
                    row: "".join(
                        cells[column].char
                        for column in sorted(cells)
                    ).rstrip()
                    for row, cells in screen.data_buffer.items()
                }
                title_row = next(
                    row for row, text in rows.items()
                    if "• Shell adb devices" in text
                )
                output_row = next(
                    row for row, text in rows.items()
                    if "└ List of devices attached" in text
                )
                header_row = next(
                    row for row, text in rows.items()
                    if ">_ App (v1.0)" in text
                )

                assert title_row - header_row == 2
                assert output_row == title_row + 1
                assert runtime.screen._process_status_height() == 0
                assert runtime.screen.input_area.filter()
                assert runtime.screen.input_footer.filter()

                positions = screen.visible_windows_to_write_positions
                input_position = positions[runtime.screen.input.window]
                bottom_padding = positions[runtime.screen.input_bottom_padding]
                footer_position = positions[runtime.screen.footer_window]
                assert footer_position.ypos == (
                    input_position.ypos
                    + input_position.height
                    + bottom_padding.height
                )
            finally:
                await runtime.close()


@pytest.mark.anyio
async def test_shell_lifecycle_never_adds_blank_rows_above_canvas() -> None:
    with create_pipe_input() as pipe_input:
        runtime = TuiRuntime(input_obj=pipe_input, output_obj=DummyOutput())

        with patch.object(
            runtime.screen.application.output,
            "get_size",
            return_value=Size(rows=12, columns=80),
        ):
            await runtime.open()
            viewer = None
            try:
                runtime.append_block(_block(">_ App (v1.0)"), kind="system")
                initial_screen = await _render_next_frame(runtime)
                initial_input = initial_screen.visible_windows_to_write_positions[
                    runtime.screen.input.window
                ]
                initial_input_row = (
                    12 - runtime.screen._visible_height() + initial_input.ypos
                )

                snapshot = {
                    "ok": True,
                    "session_id": "exec_shell",
                    "command": "adb devices",
                    "status": "running",
                    "origin": "tui_shell",
                    "output_lines": ["List of devices attached"],
                }
                live_block = exec_session_live_block(
                    snapshot,
                    terminal_width=80,
                    viewer_mode="inline",
                )
                viewer = runtime.begin_process_viewer(
                    ProcessViewerRequest(
                        fragments=PROCESS_VIEWER_FOCUS_REQUEST.fragments,
                        max_height=PROCESS_VIEWER_FOCUS_REQUEST.max_height,
                        capture_input=False,
                    ),
                    live_block,
                )
                running_screen = await _render_next_frame(runtime)
                running_input = running_screen.visible_windows_to_write_positions[
                    runtime.screen.input.window
                ]
                running_input_row = (
                    12 - runtime.screen._visible_height() + running_input.ypos
                )
                assert runtime.screen.input_area.filter()
                assert runtime.screen.input_footer.filter()
                assert runtime.screen._process_status_height() == 0

                runtime.resolve_process_viewer("exited")
                assert await viewer == "exited"
                viewer = None
                runtime.commit_process_viewer(exec_session_summary_block(
                    {**snapshot, "status": "exited", "exit_code": 0},
                    terminal_width=80,
                ))
                completed_screen = await _render_next_frame(runtime)
                completed_input = (
                    completed_screen.visible_windows_to_write_positions[
                        runtime.screen.input.window
                    ]
                )
                completed_input_row = (
                    12 - runtime.screen._visible_height() + completed_input.ypos
                )

                assert running_input_row == initial_input_row
                assert completed_input_row == initial_input_row

                for screen in (
                    initial_screen,
                    running_screen,
                    completed_screen,
                ):
                    positions = screen.visible_windows_to_write_positions
                    assert runtime.screen.canvas_spacer not in positions
                    nonblank_rows = [
                        row
                        for row, cells in screen.data_buffer.items()
                        if "".join(
                            cells[column].char
                            for column in sorted(cells)
                        ).strip()
                    ]
                    assert min(nonblank_rows) == 0
            finally:
                if runtime.screen.process_viewer.active:
                    runtime.resolve_process_viewer("detach")
                    if viewer is not None:
                        await viewer
                    runtime.dismiss_process_viewer()
                await runtime.close()


@pytest.mark.anyio
async def test_shell_submission_keeps_input_row_during_viewer_handoff() -> None:
    with create_pipe_input() as pipe_input:
        runtime = TuiRuntime(input_obj=pipe_input, output_obj=DummyOutput())

        with patch.object(
            runtime.screen.application.output,
            "get_size",
            return_value=Size(rows=12, columns=80),
        ):
            await runtime.open()
            viewer = None
            try:
                runtime.append_block(_block(">_ App (v1.0)"), kind="system")
                prompt_task = asyncio.create_task(runtime.read_message(
                    PromptContext(model="test")
                ))
                runtime.screen.input.buffer.text = "!adb devices"
                typed_screen = await _render_next_frame(runtime)
                typed_input = typed_screen.visible_windows_to_write_positions[
                    runtime.screen.input.window
                ]
                typed_input_row = (
                    12 - runtime.screen._visible_height() + typed_input.ypos
                )

                with patch.object(
                    runtime.document,
                    "stage_submission",
                    wraps=runtime.document.stage_submission,
                ) as stage_submission:
                    runtime.screen.input.buffer.validate_and_handle()
                    assert await prompt_task == "!adb devices"

                stage_submission.assert_not_called()
                submitted_screen = await _render_next_frame(runtime)
                submitted_input = (
                    submitted_screen.visible_windows_to_write_positions[
                        runtime.screen.input.window
                    ]
                )
                submitted_input_row = (
                    12 - runtime.screen._visible_height() + submitted_input.ypos
                )

                snapshot = {
                    "ok": True,
                    "session_id": "exec_shell",
                    "command": "adb devices",
                    "status": "running",
                    "origin": "tui_shell",
                    "output_lines": ["List of devices attached"],
                }
                viewer = runtime.begin_process_viewer(
                    ProcessViewerRequest(
                        fragments=PROCESS_VIEWER_FOCUS_REQUEST.fragments,
                        max_height=PROCESS_VIEWER_FOCUS_REQUEST.max_height,
                        capture_input=False,
                    ),
                    exec_session_live_block(
                        snapshot,
                        terminal_width=80,
                        viewer_mode="inline",
                    ),
                )
                viewer_screen = await _render_next_frame(runtime)
                viewer_input = viewer_screen.visible_windows_to_write_positions[
                    runtime.screen.input.window
                ]
                viewer_input_row = (
                    12 - runtime.screen._visible_height() + viewer_input.ypos
                )

                assert submitted_input_row == typed_input_row
                assert viewer_input_row == typed_input_row
            finally:
                if runtime.screen.process_viewer.active:
                    runtime.resolve_process_viewer("detach")
                    if viewer is not None:
                        await viewer
                    runtime.dismiss_process_viewer()
                await runtime.close()


@pytest.mark.anyio
async def test_inline_shell_detaches_before_next_submission_is_staged() -> None:
    with create_pipe_input() as pipe_input:
        runtime = TuiRuntime(input_obj=pipe_input, output_obj=DummyOutput())

        with patch.object(
            runtime.screen.application.output,
            "get_size",
            return_value=Size(rows=12, columns=80),
        ):
            await runtime.open()
            settle_task = None
            try:
                runtime.append_block(_block(">_ App (v1.0)"), kind="system")
                viewer = runtime.begin_process_viewer(
                    ProcessViewerRequest(
                        fragments=PROCESS_VIEWER_FOCUS_REQUEST.fragments,
                        max_height=PROCESS_VIEWER_FOCUS_REQUEST.max_height,
                        capture_input=False,
                    ),
                    exec_session_live_block(
                        {
                            "ok": True,
                            "session_id": "exec_shell",
                            "command": "ping -t 8.8.8.8",
                            "status": "running",
                            "origin": "tui_shell",
                            "output_lines": ["reply"],
                        },
                        terminal_width=80,
                        viewer_mode="inline",
                    ),
                )

                async def settle_viewer() -> None:
                    assert await viewer == "detach"
                    runtime.commit_process_viewer(_block(
                        "• Shell ping -t 8.8.8.8\n└ reply"
                    ))

                settle_task = asyncio.create_task(settle_viewer())
                prompt_task = asyncio.create_task(runtime.read_message(
                    PromptContext(model="test")
                ))
                runtime.screen.input.buffer.text = "/resume"
                active_screen = await _render_next_frame(runtime)
                active_input = active_screen.visible_windows_to_write_positions[
                    runtime.screen.input.window
                ]
                active_input_row = (
                    12 - runtime.screen._visible_height() + active_input.ypos
                )

                stage_states: list[bool] = []
                stage_submission = runtime.document.stage_submission

                def stage_after_detach(*args, **kwargs) -> None:
                    stage_states.append(runtime.screen.process_viewer.active)
                    stage_submission(*args, **kwargs)

                with patch.object(
                    runtime.document,
                    "stage_submission",
                    side_effect=stage_after_detach,
                ):
                    runtime.screen.input.buffer.validate_and_handle()
                    assert await prompt_task == "/resume"

                await settle_task
                settle_task = None
                submitted_screen = await _render_next_frame(runtime)
                submitted_input = (
                    submitted_screen.visible_windows_to_write_positions[
                        runtime.screen.input.window
                    ]
                )
                submitted_input_row = (
                    12 - runtime.screen._visible_height() + submitted_input.ypos
                )

                assert stage_states == [False]
                assert submitted_input_row == active_input_row
            finally:
                if settle_task is not None:
                    settle_task.cancel()
                    await asyncio.gather(settle_task, return_exceptions=True)
                await runtime.close()


@pytest.mark.anyio
@pytest.mark.parametrize("menu_level", (2, 3, 4))
async def test_nested_menu_starts_at_query_input_offset(
    menu_level: int,
) -> None:
    with create_pipe_input() as pipe_input:
        runtime = TuiRuntime(input_obj=pipe_input, output_obj=DummyOutput())

        with patch.object(
            runtime.screen.application.output,
            "get_size",
            return_value=Size(rows=16, columns=60),
        ):
            await runtime.open()
            menu_task = None
            try:
                runtime.append_block(
                    query_block("choose an option"),
                    kind="user",
                )
                screen = await _render_next_frame(runtime)
                positions = screen.visible_windows_to_write_positions
                input_position = positions[runtime.screen.input.window]
                input_transcript = positions[runtime.screen.transcript_window]
                input_offset = (
                    input_position.ypos
                    - input_transcript.ypos
                    - input_transcript.height
                )

                menu_task = asyncio.create_task(runtime.select_menu(
                    MenuRequest(
                        title=f"Level {menu_level}",
                        options=tuple(
                            MenuOption(index, f"Option {index}")
                            for index in range(1, menu_level + 1)
                        ),
                    ),
                ))
                await asyncio.sleep(0)
                screen = await _render_next_frame(runtime)
                positions = screen.visible_windows_to_write_positions
                menu_position = positions[runtime.screen.menu_window]
                menu_transcript = positions[runtime.screen.transcript_window]
                menu_padding = positions[runtime.screen.menu_top_padding]
                menu_offset = (
                    menu_position.ypos
                    - menu_transcript.ypos
                    - menu_transcript.height
                )

                assert input_offset == 1
                assert menu_offset == input_offset
                assert menu_position.ypos == (
                    menu_padding.ypos + menu_padding.height
                )
            finally:
                if runtime.screen.menu.active:
                    runtime.screen.menu.finish(None)
                if menu_task is not None:
                    await menu_task
                await runtime.close()


@pytest.mark.anyio
async def test_menu_top_padding_is_not_retained_after_result() -> None:
    with create_pipe_input() as pipe_input:
        runtime = TuiRuntime(input_obj=pipe_input, output_obj=DummyOutput())

        with patch.object(
            runtime.screen.application.output,
            "get_size",
            return_value=Size(rows=16, columns=60),
        ):
            await runtime.open()
            menu_task = None
            try:
                menu_task = asyncio.create_task(runtime.select_menu(
                    MenuRequest(
                        title="External MCP",
                        options=tuple(
                            MenuOption(index, f"Option {index}")
                            for index in range(5)
                        ),
                    ),
                ))
                await asyncio.sleep(0)
                screen = await _render_next_frame(runtime)
                positions = screen.visible_windows_to_write_positions
                menu_padding = positions[runtime.screen.menu_top_padding]
                menu_height = runtime.screen._visible_height()

                runtime.screen.menu.finish("stop")
                assert await menu_task == "stop"
                menu_task = None

                screen = await _render_next_frame(runtime)
                positions = screen.visible_windows_to_write_positions
                closed_input = positions[runtime.screen.input.window]
                closed_input_row = (
                    16
                    - runtime.screen._visible_height()
                    + closed_input.ypos
                )

                assert runtime.screen._visible_height() == (
                    menu_height - menu_padding.height
                )

                runtime.append_block(
                    _block("■ External MCP already stopped"),
                    kind="operation",
                )
                screen = await _render_next_frame(runtime)
                positions = screen.visible_windows_to_write_positions
                result_input = positions[runtime.screen.input.window]
                result_input_row = (
                    16
                    - runtime.screen._visible_height()
                    + result_input.ypos
                )
                rows = {
                    row: "".join(
                        cells[column].char for column in sorted(cells)
                    ).rstrip()
                    for row, cells in screen.data_buffer.items()
                }
                result_row = next(
                    row
                    for row, text in rows.items()
                    if "External MCP already stopped" in text
                )

                assert result_input_row == closed_input_row + 1
                assert result_input.ypos - result_row == 3
                assert runtime.screen.canvas_spacer not in positions
            finally:
                if runtime.screen.menu.active:
                    runtime.screen.menu.finish(None)
                if menu_task is not None:
                    await menu_task
                await runtime.close()


@pytest.mark.anyio
@pytest.mark.parametrize("tail_kind", ("user", "assistant"))
async def test_process_viewer_starts_at_query_input_offset(
    tail_kind: str,
) -> None:
    with create_pipe_input() as pipe_input:
        runtime = TuiRuntime(input_obj=pipe_input, output_obj=DummyOutput())

        with patch.object(
            runtime.screen.application.output,
            "get_size",
            return_value=Size(rows=16, columns=60),
        ):
            await runtime.open()
            viewer_future = None
            try:
                block = (
                    query_block("run a command")
                    if tail_kind == "user"
                    else _block("previous answer")
                )
                runtime.append_block(block, kind=tail_kind)
                screen = await _render_next_frame(runtime)
                positions = screen.visible_windows_to_write_positions
                transcript = positions[runtime.screen.transcript_window]
                input_position = positions[runtime.screen.input.window]
                input_offset = (
                    input_position.ypos
                    - transcript.ypos
                    - transcript.height
                )

                viewer_future = runtime.screen.process_viewer.begin(
                    ProcessViewerRequest(
                        fragments=(("", "Process running"),),
                        max_height=1,
                    )
                )
                screen = await _render_next_frame(runtime)
                positions = screen.visible_windows_to_write_positions
                transcript = positions[runtime.screen.transcript_window]
                viewer_position = positions[
                    runtime.screen.process_viewer_window
                ]
                viewer_padding = positions[
                    runtime.screen.process_viewer_top_padding
                ]
                viewer_offset = (
                    viewer_position.ypos
                    - transcript.ypos
                    - transcript.height
                )

                assert viewer_offset == input_offset
                assert viewer_position.ypos == (
                    viewer_padding.ypos + viewer_padding.height
                )
                assert runtime.screen.canvas_spacer not in positions
            finally:
                if runtime.screen.process_viewer.active:
                    runtime.screen.process_viewer.resolve("detach")
                    if viewer_future is not None:
                        assert await viewer_future == "detach"
                    runtime.screen.process_viewer.settle()
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
@pytest.mark.parametrize("render_each_key", (False, True))
async def test_multiline_input_backspace_shrinks_without_top_spacer(
    render_each_key: bool,
) -> None:
    with create_pipe_input() as pipe_input:
        runtime = TuiRuntime(input_obj=pipe_input, output_obj=DummyOutput())

        with patch.object(
            runtime.screen.application.output,
            "get_size",
            return_value=Size(rows=16, columns=40),
        ):
            await runtime.open()
            try:
                initial_height = runtime.screen._visible_height()

                if render_each_key:
                    pipe_input.send_text("x")
                    await _wait_for_input_text(runtime, "x")
                    for line_count in range(1, 9):
                        pipe_input.send_text("\x0f")
                        await _wait_for_input_text(
                            runtime,
                            "x" + "\n" * line_count,
                        )
                        await _render_next_frame(runtime)
                else:
                    pipe_input.send_text("x" + "\x0f" * 8)
                    await _wait_for_input_text(runtime, "x" + "\n" * 8)

                await _render_next_frame(runtime)
                expanded_height = runtime.screen._visible_height()
                assert expanded_height > initial_height

                if render_each_key:
                    for line_count in range(7, -1, -1):
                        pipe_input.send_text("\x7f")
                        await _wait_for_input_text(
                            runtime,
                            "x" + "\n" * line_count,
                        )
                        screen = await _render_next_frame(runtime)
                        positions = screen.visible_windows_to_write_positions

                        assert (
                            runtime.screen._visible_height()
                            == runtime.screen._natural_visible_height()
                        )
                        assert runtime.screen.canvas_spacer not in positions
                else:
                    pipe_input.send_text("\x7f" * 8)
                    await _wait_for_input_text(runtime, "x")

                screen = await _render_next_frame(runtime)
                positions = screen.visible_windows_to_write_positions
                footer = positions[runtime.screen.footer_window]

                assert runtime.screen._natural_visible_height() == initial_height
                assert runtime.screen._visible_height() == initial_height
                assert runtime.screen.canvas_spacer not in positions
                assert footer.ypos + footer.height == initial_height

                pipe_input.send_text("\x7f")
                await _wait_for_input_text(runtime, "")

                screen = await _render_next_frame(runtime)
                positions = screen.visible_windows_to_write_positions

                assert runtime.screen._visible_height() == initial_height
                assert (
                    runtime.screen._canvas_height_floor
                    == runtime.screen._natural_visible_height()
                )
                assert runtime.screen.canvas_spacer not in positions
            finally:
                await runtime.close()


@pytest.mark.anyio
async def test_multiline_input_undo_shrinks_without_top_spacer() -> None:
    with create_pipe_input() as pipe_input:
        runtime = TuiRuntime(input_obj=pipe_input, output_obj=DummyOutput())

        with patch.object(
            runtime.screen.application.output,
            "get_size",
            return_value=Size(rows=16, columns=40),
        ):
            await runtime.open()
            try:
                initial_height = runtime.screen._visible_height()
                buffer = runtime.screen.input.buffer

                buffer.text = "first"
                buffer.cursor_position = len(buffer.text)
                buffer.save_to_undo_stack()

                buffer.text = "first\nsecond\nthird"
                buffer.cursor_position = len(buffer.text)
                await _render_next_frame(runtime)

                assert runtime.screen._visible_height() > initial_height

                pipe_input.send_text("\x1a")
                await _wait_for_input_text(runtime, "first")

                screen = await _render_next_frame(runtime)
                positions = screen.visible_windows_to_write_positions

                assert runtime.screen._visible_height() == initial_height
                assert (
                    runtime.screen._canvas_height_floor
                    == runtime.screen._natural_visible_height()
                )
                assert runtime.screen.canvas_spacer not in positions
            finally:
                await runtime.close()


@pytest.mark.anyio
async def test_ctrl_u_clears_multiline_input_without_top_canvas_spacer() -> None:
    with create_pipe_input() as pipe_input:
        runtime = TuiRuntime(input_obj=pipe_input, output_obj=DummyOutput())

        with patch.object(
            runtime.screen.application.output,
            "get_size",
            return_value=Size(rows=16, columns=40),
        ):
            await runtime.open()
            try:
                runtime.append_block(_block("transcript line"), kind="assistant")
                await _render_next_frame(runtime)
                initial_height = runtime.screen._visible_height()

                value = "\n".join(f"line {index}" for index in range(8))
                runtime.screen.input.buffer.text = value
                runtime.screen.input.buffer.cursor_position = len(value)
                await _render_next_frame(runtime)

                assert runtime.screen._visible_height() > initial_height

                pipe_input.send_text("\x15")
                await _wait_for_input_text(runtime, "")

                screen = await _render_next_frame(runtime)
                positions = screen.visible_windows_to_write_positions

                assert runtime.screen._input_height() == 1
                assert runtime.screen._visible_height() == initial_height
                assert (
                    runtime.screen._canvas_height_floor
                    == runtime.screen._natural_visible_height()
                )
                assert runtime.screen.canvas_spacer not in positions
            finally:
                await runtime.close()


@pytest.mark.anyio
async def test_folded_multiline_paste_keeps_input_at_canvas_bottom() -> None:
    with create_pipe_input() as pipe_input:
        runtime = TuiRuntime(input_obj=pipe_input, output_obj=DummyOutput())

        with patch.object(
            runtime.screen.application.output,
            "get_size",
            return_value=Size(rows=16, columns=40),
        ):
            await runtime.open()
            try:
                initial_height = runtime.screen._visible_height()
                pasted = "\n".join(f"line {index}" for index in range(20))

                runtime.screen.input.buffer.text = pasted
                runtime.screen.input.buffer.cursor_position = len(pasted)
                await _render_next_frame(runtime)
                expanded_height = runtime.screen._visible_height()

                placeholder = runtime.input_model._display_paste(pasted, "")
                runtime.screen.input.buffer.text = placeholder
                runtime.screen.input.buffer.cursor_position = len(placeholder)
                screen = await _render_next_frame(runtime)
                positions = screen.visible_windows_to_write_positions
                spacer = positions[runtime.screen.canvas_spacer]
                footer = positions[runtime.screen.footer_window]

                assert runtime.screen._input_height() == 1
                assert runtime.screen._natural_visible_height() == initial_height
                assert runtime.screen._visible_height() == expanded_height
                assert spacer.height == expanded_height - initial_height
                assert footer.ypos + footer.height == expanded_height
            finally:
                await runtime.close()


@pytest.mark.anyio
async def test_multiline_input_backspace_keeps_retired_canvas_collapsed() -> None:
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
                assert runtime.screen._canvas_height_floor == 12

                pipe_input.send_text("\x0f" * 8)
                await _wait_for_input_text(runtime, "\n" * 8)

                pipe_input.send_text("\x7f" * 8)
                await _wait_for_input_text(runtime, "")

                screen = await _render_next_frame(runtime)

                assert runtime.document.scrollback_line_count > 0
                assert runtime.screen._canvas_height_floor == (
                    runtime.screen._natural_visible_height()
                )
                assert runtime.screen._visible_height() == (
                    runtime.screen._natural_visible_height()
                )
                assert runtime.screen.canvas_spacer not in (
                    screen.visible_windows_to_write_positions
                )
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
                with (
                    patch.object(
                        runtime.screen,
                        "begin_synchronized_output",
                        return_value=True,
                    ) as begin_synchronized,
                    patch.object(
                        runtime.screen,
                        "end_synchronized_output",
                    ) as end_synchronized,
                ):
                    runtime.screen.input.buffer.validate_and_handle()
                    runtime.screen.input.buffer.validate_and_handle()

                    assert await prompt_task == "first\nsecond\nthird"
                    assert runtime.submissions.message_queue.empty()

                    for _ in range(20):
                        await asyncio.sleep(0)
                        if end_synchronized.called:
                            break

                    begin_synchronized.assert_called_once_with()
                    end_synchronized.assert_called_once_with()

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

                await output.prepare_external_output()

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
async def test_approval_dismissal_height_is_consumed_by_stream(
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
                normal_screen = await _render_next_frame(runtime)
                normal_input = normal_screen.visible_windows_to_write_positions[
                    runtime.screen.input.window
                ]
                normal_input_row = (
                    12
                    - runtime.screen._visible_height()
                    + normal_input.ypos
                )

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
                positions = screen.visible_windows_to_write_positions
                input_position = positions[runtime.screen.input.window]
                input_row = (
                    12
                    - runtime.screen._visible_height()
                    + input_position.ypos
                )
                release_height = runtime.screen._bottom_release_height()

                assert release_height > 0
                assert input_row < normal_input_row
                assert runtime.screen.canvas_spacer not in positions

                previous_row = input_row
                for line_count in range(2, release_height + 2):
                    runtime.set_active_renderable(
                        _block("\n".join(
                            f"answer {index}"
                            for index in range(line_count)
                        )),
                        kind="assistant",
                    )
                    screen = await _render_next_frame(runtime)
                    positions = screen.visible_windows_to_write_positions
                    input_position = positions[runtime.screen.input.window]
                    input_row = (
                        12
                        - runtime.screen._visible_height()
                        + input_position.ypos
                    )

                    assert input_row == previous_row + 1
                    assert runtime.screen.canvas_spacer not in positions
                    previous_row = input_row

                footer_position = positions[
                    runtime.screen.footer_window
                ]

                assert input_row == normal_input_row
                assert runtime.screen._bottom_release_height() == 0
                assert footer_position.ypos + footer_position.height == (
                    runtime.screen._visible_height()
                )
            finally:
                await runtime.close()


@pytest.mark.anyio
async def test_approval_keeps_only_query_and_card_padding_after_wait_status(
) -> None:
    with create_pipe_input() as pipe_input:
        output = _AlternateScreenOutput(columns=80, rows=24)
        runtime = TuiRuntime(input_obj=pipe_input, output_obj=output)
        await runtime.open()
        approval_task = None
        try:
            runtime.append_block(
                query_block("执行adb devices"),
                kind="user",
            )
            runtime.set_execution_active(True)
            await runtime.begin_wait_status()
            await _render_next_frame(runtime)

            approval_task = asyncio.create_task(runtime.request_approval({
                "tool": "shell_command",
                "command": "adb devices",
                "show_timer": False,
            }))

            for _ in range(100):
                await asyncio.sleep(0)
                if (
                    runtime.screen.approval.active
                    and runtime.screen.activity_block is None
                ):
                    break
            else:
                raise AssertionError("approval did not settle")

            screen = await _render_next_frame(runtime)
            positions = screen.visible_windows_to_write_positions
            transcript = positions[runtime.screen.transcript_window]
            approval = positions[runtime.screen.approval_window]

            nonblank_rows = {
                row: "".join(
                    cells[column].char for column in sorted(cells)
                ).rstrip()
                for row, cells in screen.data_buffer.items()
            }
            query_row = next(
                row
                for row, text in nonblank_rows.items()
                if "执行adb devices" in text
            )
            question_row = next(
                row
                for row, text in nonblank_rows.items()
                if "Would you like" in text
            )

            assert approval.ypos == transcript.ypos + transcript.height
            assert question_row - query_row == 4
            assert all(
                not nonblank_rows.get(row)
                for row in range(query_row + 1, question_row)
            )
            assert runtime.screen.canvas_spacer not in positions
            assert not runtime.screen._content_input_gap_visible()
        finally:
            if runtime.screen.approval.active:
                runtime.screen.approval.finish("decline")
            if approval_task is not None:
                await approval_task
            runtime.set_execution_active(False)
            await runtime.activity.clear()
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

                assert runtime.screen._canvas_height_floor == 12

                pipe_input.send_text("\x0c")
                for _ in range(100):
                    await asyncio.sleep(0.01)
                    if not runtime.document.has_visible_content:
                        break
                else:
                    raise AssertionError("Ctrl+L did not clear the transcript")

                screen = await _render_next_frame(runtime)
                positions = screen.visible_windows_to_write_positions
                top_padding = positions[runtime.screen.input_top_padding]
                input_position = positions[runtime.screen.input.window]

                assert runtime.screen._visible_height() == (
                    runtime.screen._natural_visible_height()
                )
                assert runtime.screen.canvas_spacer not in positions
                assert top_padding.ypos == 0
                assert input_position.ypos == top_padding.height
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
    monkeypatch.setattr("mind_app.tui.core.screen.sys.platform", "win32")
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
        runtime._discard_submitted_query()

    assert content_changed.call_count == 1
    assert "new question" not in "".join(
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
async def test_js_repl_start_and_result_are_two_separated_blocks() -> None:
    runtime = TuiRuntime()
    output = TuiOutputControl("", runtime=runtime, animate=False)
    presentation = TuiPresentationSink(output)
    arguments = {
        "code": "await host.tool('shell_command', {command: 'echo ready'});",
        "timeout_ms": 30000,
    }

    await presentation.emit(build_tool_start_view(
        "js_repl",
        arguments,
        call_id="call-js",
    ))

    assert len(runtime.document.blocks) == 1
    assert "host.tool('shell_command'" in _document_text(runtime.document)

    await presentation.emit(build_native_tool_result_view(
        "js_repl",
        arguments,
        ok=True,
        data={"output": "ready"},
        call_id="call-js",
    ))

    assert [item.kind for item in runtime.document.blocks] == [
        "operation",
        "operation",
    ]
    assert "host.tool('shell_command'" in runtime.document.blocks[0].raw_text
    assert "ready" in runtime.document.blocks[1].raw_text
    document_text = _document_text(runtime.document)
    transcript_text = _transcript_text(runtime.document)
    assert document_text.count("• JavaScript") == 2
    assert "host.tool('shell_command'" in document_text
    assert "\n└ ready" in document_text
    assert transcript_text.count("• JavaScript") == 2
    assert "host.tool('shell_command'" in transcript_text
    assert "\n└ ready" in transcript_text
    assert "\n\n• JavaScript\n└ ready" in document_text
    assert [item.gap_before for item in runtime.document.blocks] == [
        False,
        True,
    ]
    assert isinstance(runtime.document.blocks[0].source, ToolStartView)
    assert isinstance(runtime.document.blocks[1].source, NativeToolResultView)

    await presentation.emit(build_tool_start_view(
        "js_repl",
        {"code": "console.log('next');", "timeout_ms": 30000},
        call_id="call-js-next",
    ))
    await presentation.emit(build_native_tool_result_view(
        "js_repl",
        {"code": "console.log('next');", "timeout_ms": 30000},
        ok=True,
        data={"output": "next"},
        call_id="call-js-next",
    ))

    document_text = _document_text(runtime.document)
    assert "ready\n\n• JavaScript" in document_text
    assert [item.gap_before for item in runtime.document.blocks] == [
        False,
        True,
        True,
        True,
    ]


@pytest.mark.anyio
async def test_js_repl_query_padding_and_two_stage_display() -> None:
    runtime = TuiRuntime()
    output = TuiOutputControl("", runtime=runtime, animate=False)
    presentation = TuiPresentationSink(output)
    javascript_arguments = {
        "code": "await host.tool('shell_command', {command: 'echo ready'});\n\n",
        "timeout_ms": 30000,
    }
    runtime.append_block(
        query_block(
            "js repl执行\n"
            + javascript_arguments["code"].strip("\n")
        ),
        kind="user",
    )

    await presentation.emit(build_tool_start_view(
        "js_repl",
        javascript_arguments,
        call_id="outer-js",
    ))

    running_text = _document_text(runtime.document)
    assert len(runtime.document.blocks) == 2
    assert running_text.count("• JavaScript") == 1
    assert "Running" not in running_text
    assert "Ran" not in running_text
    lines = [
        fragments_text(line)
        for line in split_formatted_lines(runtime.document.fragments(width=80))
    ]
    javascript_line = lines.index("• JavaScript")
    assert lines[javascript_line - 2:javascript_line] == [" ", " "]
    assert lines[javascript_line - 3] != " "
    await presentation.emit(build_native_tool_result_view(
        "js_repl",
        javascript_arguments,
        ok=True,
        data={"output": ""},
        call_id="outer-js",
    ))

    completed_text = _document_text(runtime.document)
    assert len(runtime.document.blocks) == 3
    assert completed_text.count("• JavaScript") == 2
    assert "Running" not in completed_text
    assert "Ran" not in completed_text
    assert "JavaScript cell completed." in completed_text
    assert [item.gap_before for item in runtime.document.blocks] == [
        False,
        True,
        True,
    ]
    assert [type(item.source) for item in runtime.document.blocks[1:]] == [
        ToolStartView,
        NativeToolResultView,
    ]

    await output.append_assistant_delta("已执行完成。")
    await output.prepare_external_output()

    final_text = _document_text(runtime.document)
    assert "JavaScript cell completed.\n\n• 已执行完成。" in final_text


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

    assert runtime.document.active_block is None

    await output.settle_stream()

    active = runtime.document.active_block
    assert active is not None
    active_lines = fragments_text(active.fragments).splitlines()
    assert "".join(line[2:] for line in active_lines) == source
    assert " ..." not in fragments_text(active.fragments)

    await output.prepare_external_output()

    assert _document_text(runtime.document) == f"• {source}"


@pytest.mark.anyio
async def test_wide_character_stream_reflows_without_losing_content() -> None:
    runtime = TuiRuntime()
    output = TuiOutputControl("", runtime=runtime, animate=False)
    source = "中文" * 60

    await output.append_assistant_delta(source)

    assert runtime.document.active_block is None

    await output.settle_stream()
    active = runtime.document.active_block
    assert active is not None
    active_lines = fragments_text(active.fragments).splitlines()
    assert "".join(line[2:] for line in active_lines) == source


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
    await output.settle_stream()
    output.mark_stream_boundary()
    await output.append_assistant_delta("second")
    await output.settle_stream()

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
    await output.settle_stream()
    output.mark_stream_boundary()
    await output.append_assistant_delta(second)
    await output.settle_stream()

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
async def test_animated_stream_waits_for_complete_source_line() -> None:
    runtime = TuiRuntime()
    output = TuiOutputControl("", runtime=runtime, animate=True)

    with (
        patch.object(
            runtime,
            "set_active_renderable",
            wraps=runtime.set_active_renderable,
        ) as update,
        patch(
            "mind_app.tui.adapters.markdown.render_tui_markdown",
            wraps=render_tui_markdown,
        ) as render,
    ):
        await output.append_assistant_delta("**first")
        await output.append_assistant_delta(" second")
        await output.append_assistant_delta(" third**")

        assert output.assistant.text == "**first second third**"
        assert runtime.document.active_block is None
        assert update.call_count == 0
        render.assert_not_called()

        await output.settle_stream()
        assert update.call_count == 1
        render.assert_not_called()
        await asyncio.sleep(0.04)
        assert update.call_count == 1

        await output.prepare_external_output()
        render.assert_called_once_with(
            "**first second third**",
            hyperlinks=runtime.hyperlinks_enabled,
            width=max(1, runtime.terminal_width - 2),
        )

    assert _document_text(runtime.document) == "• first second third"


@pytest.mark.anyio
async def test_stream_keeps_one_plain_active_block_without_losing_source() -> None:
    runtime = TuiRuntime()
    output = TuiOutputControl("", runtime=runtime, animate=False)
    source = "\n".join(f"line {index}" for index in range(12))

    await output.append_assistant_delta(source)

    assert not runtime.document.blocks
    assert not runtime.document.active_stream_continuation
    assert output._active_stream_text() == source
    assert _document_text(runtime.document) == "\n".join([
        "• line 0",
        *(f"  line {index}" for index in range(1, 11)),
    ])

    await output.prepare_external_output()

    cells = runtime.document.blocks
    assert len(cells) == 1
    assert cells[0].raw_text == source
    assert not cells[0].stream_continuation
    assert _document_text(runtime.document) == "\n".join([
        "• line 0",
        *(f"  line {index}" for index in range(1, 12)),
    ])


@pytest.mark.anyio
async def test_live_stream_tail_keeps_one_assistant_prefix() -> None:
    runtime = TuiRuntime()
    output = TuiOutputControl("", runtime=runtime, animate=False)

    for index in range(8):
        await output.append_assistant_delta(
            ("" if index == 0 else "\n") + f"line {index}"
        )

        expected = "\n".join([
            "• line 0",
            *(f"  line {line}" for line in range(1, index)),
        ]) if index else ""
        assert _document_text(runtime.document) == expected

    await output.settle_stream()
    assert _document_text(runtime.document).endswith("  line 7")


@pytest.mark.anyio
async def test_live_stream_tail_is_rendered_before_it_appears() -> None:
    runtime = TuiRuntime()
    output = TuiOutputControl("", runtime=runtime, animate=False)
    source = "\n".join(
        f"**line {index}** [docs](https://example.com/{index})"
        for index in range(4)
    )

    with patch(
        "mind_app.tui.adapters.markdown.render_tui_markdown",
        wraps=render_tui_markdown,
    ) as render:
        await output.append_assistant_delta(source)

        render.assert_not_called()
        active = runtime.document.active_block
        assert active is not None
        text = fragments_text(active.fragments)
        assert "**" not in text
        assert "https://" not in text
        assert "line 2 docs" in text
        assert "line 3 docs" not in text
        assert any(
            "bold" in style
            for style, value in active.fragments
            if value.strip()
        )

        await output.settle_stream()

        render.assert_not_called()
        active = runtime.document.active_block
        assert active is not None
        assert "line 3 docs" in fragments_text(active.fragments)

        await output.prepare_external_output()

        render.assert_called_once_with(
            source,
            hyperlinks=runtime.hyperlinks_enabled,
            width=max(1, runtime.terminal_width - 2),
        )

    settled = runtime.document.blocks[-1].display_block
    text = fragments_text(settled.fragments)
    assert "**" not in text
    assert "https://" not in text
    assert all(f"line {index} docs" in text for index in range(4))
    assert any(
        "bold" in style
        for style, value in settled.fragments
        if value.strip()
    )
    assert any(
        "underline" in style
        for style, value in settled.fragments
        if value.strip()
    )


@pytest.mark.anyio
async def test_stream_markdown_tail_keeps_input_anchor_without_slack() -> None:
    with create_pipe_input() as pipe_input:
        runtime = TuiRuntime(input_obj=pipe_input, output_obj=DummyOutput())
        output = TuiOutputControl("", runtime=runtime, animate=False)

        with patch.object(
            runtime.screen.application.output,
            "get_size",
            return_value=Size(rows=24, columns=40),
        ):
            await runtime.open()
            try:
                runtime.set_execution_active(True)
                await output.append_assistant_delta(
                    f"[link](https://example.com/{'q' * 400})"
                )
                screen = await _render_next_frame(runtime)
                position = screen.visible_windows_to_write_positions[
                    runtime.screen.input.window
                ]
                input_row = (
                    24 - runtime.screen._visible_height() + position.ypos
                )

                await output.append_assistant_delta("\na\nb\nc\n")
                screen = await _render_next_frame(runtime)
                positions = screen.visible_windows_to_write_positions
                position = positions[runtime.screen.input.window]
                transcript = positions[runtime.screen.transcript_window]
                top_padding = positions[runtime.screen.input_top_padding]

                assert (
                    24
                    - runtime.screen._visible_height()
                    + position.ypos
                    == input_row
                )
                assert runtime.screen.canvas_spacer not in positions
                assert top_padding.ypos == (
                    transcript.ypos + transcript.height
                )
            finally:
                runtime.set_execution_active(False)
                await runtime.close()


@pytest.mark.anyio
async def test_final_stream_renders_full_markdown_in_one_block() -> None:
    runtime = TuiRuntime()
    output = TuiOutputControl("", runtime=runtime, animate=False)
    source = "\n".join(f"**line {index}**" for index in range(12))

    await output.append_assistant_delta(source)
    assert not runtime.document.blocks

    await output.prepare_external_output()

    assert len(runtime.document.blocks) == 1
    assert runtime.document.blocks[0].raw_text == source
    fragments = [
        fragment
        for cell in runtime.document.blocks
        for fragment in cell.display_block.fragments
    ]
    assert "**" not in "".join(text for _style, text in fragments)
    assert all(
        any("bold" in style and text == f"line {index}" for style, text in fragments)
        for index in range(12)
    )


@pytest.mark.anyio
async def test_animated_stream_drains_backlog_without_more_deltas() -> None:
    runtime = TuiRuntime()
    output = TuiOutputControl("", runtime=runtime, animate=True)
    source = "中文" * 50 + "\n"

    await output.append_assistant_delta(source)

    assert output.assistant.text == source
    assert 0 < output._stream_visible_rows < len(output._stream_rows)

    for _ in range(50):
        if output._stream_visible_rows == len(output._stream_rows):
            break
        await asyncio.sleep(0.01)

    assert output._stream_visible_rows == len(output._stream_rows)
    assert output._stream_render_handle is None

    await output.settle_stream()
    visible_lines = _document_text(runtime.document).splitlines()
    assert "".join(line[2:] for line in visible_lines) == source.rstrip("\n")


@pytest.mark.anyio
async def test_incomplete_animated_line_never_creates_a_transient_block() -> None:
    runtime = TuiRuntime()
    output = TuiOutputControl("", runtime=runtime, animate=True)
    await output.append_assistant_delta("done")

    assert runtime.document.active_block is None

    await asyncio.sleep(0.12)

    assert runtime.document.active_block is None

    await output.settle_stream()

    assert _document_text(runtime.document) == "• done"


@pytest.mark.anyio
async def test_settled_stream_skips_an_identical_visible_tail_update() -> None:
    runtime = TuiRuntime()
    output = TuiOutputControl("", runtime=runtime, animate=False)

    with patch.object(
        runtime,
        "set_active_renderable",
        wraps=runtime.set_active_renderable,
    ) as update:
        await output.append_assistant_delta("done\n")
        revision = runtime.document.active_transcript_revision

        await output.settle_stream()

    assert update.call_count == 1
    assert runtime.document.active_transcript_revision == revision


def test_final_stream_handoff_merges_render_requests() -> None:
    runtime = TuiRuntime()
    output = TuiOutputControl("", runtime=runtime, animate=False)
    output.assistant.append("final line")

    with patch.object(runtime.screen, "_invalidate_now") as invalidate:
        output._commit_current()

    invalidate.assert_called_once_with()
    assert runtime.document.active_block is None
    assert _document_text(runtime.document) == "• final line"


@pytest.mark.anyio
async def test_output_stop_unregisters_the_stream_resize_callback() -> None:
    runtime = TuiRuntime()
    output = TuiOutputControl("", runtime=runtime, animate=False)
    event = runtime.screen.application.before_render

    assert output._sync_stream_width in event._handlers

    await output.stop()

    assert output._sync_stream_width not in event._handlers


@pytest.mark.anyio
@pytest.mark.parametrize(
    "source",
    (
        "x" * 77,
        "first\n   \nsecond",
        "```\nvalue\n```\n\n- item " + "x" * 40,
        (
            "| Name | Value |\n"
            "|---|---|\n"
            "| short | one |\n"
            f"| Longer Name | {'x' * 50} |"
        ),
        "last line without newline",
    ),
)
async def test_final_stream_handoff_preserves_the_rendered_frame(
    source: str,
) -> None:
    with create_pipe_input() as pipe_input:
        runtime = TuiRuntime(input_obj=pipe_input, output_obj=DummyOutput())
        output = TuiOutputControl("", runtime=runtime, animate=False)

        with patch.object(
            runtime.screen.application.output,
            "get_size",
            return_value=Size(rows=60, columns=40),
        ):
            await runtime.open()
            try:
                await output.append_assistant_delta(source)
                await output.settle_stream()
                streamed_screen = await _render_next_frame(runtime)

                def frame_signature(screen) -> tuple[typing.Any, ...]:
                    positions = screen.visible_windows_to_write_positions
                    transcript = positions[runtime.screen.transcript_window]
                    input_position = positions[runtime.screen.input.window]
                    rows = tuple(
                        tuple(
                            (
                                screen.data_buffer[row][column].char,
                                screen.data_buffer[row][column].style,
                            )
                            for column in range(40)
                        )
                        for row in range(
                            transcript.ypos,
                            transcript.ypos + transcript.height,
                        )
                    )
                    return (
                        transcript.ypos,
                        transcript.height,
                        input_position.ypos,
                        rows,
                    )

                streamed = frame_signature(streamed_screen)
                frames: list[tuple[typing.Any, ...]] = []

                def capture_frame(_application) -> None:
                    rendered = (
                        runtime.screen.application.renderer.last_rendered_screen
                    )
                    positions = rendered.visible_windows_to_write_positions
                    if (
                        runtime.screen.transcript_window not in positions
                        or runtime.screen.input.window not in positions
                    ):
                        return None
                    frames.append(frame_signature(rendered))

                runtime.screen.application.after_render += capture_frame
                output._commit_current()

                for _ in range(20):
                    await asyncio.sleep(0)
                    if frames:
                        break

                assert frames
                assert all(frame == streamed for frame in frames)
            finally:
                await runtime.close()


@pytest.mark.anyio
async def test_partial_delta_does_not_mutate_visible_completed_line() -> None:
    runtime = TuiRuntime()
    output = TuiOutputControl("", runtime=runtime, animate=True)
    await output.append_assistant_delta("done\n")
    visible = _document_text(runtime.document)

    await output.append_assistant_delta(" next")

    assert _document_text(runtime.document) == visible

    await output.settle_stream()
    assert _document_text(runtime.document) == "• done\n  next"


def test_stream_render_budget_adapts_to_size_and_render_cost() -> None:
    output = TuiOutputControl("", runtime=TuiRuntime(), animate=True)

    output.assistant.text = "short"
    assert output._stream_render_interval() == 1 / 20

    output._stream_render_cost_sec = 1 / 80
    assert output._stream_render_interval() == 1 / 12

    output._stream_render_cost_sec = 0.0
    output.assistant.text = "x" * 2000
    assert output._stream_render_interval() == 1 / 12

    output.assistant.text = "x" * 50_000
    assert output._stream_render_interval() == 1 / 8


def test_transcript_prefix_reuses_revision_cache() -> None:
    runtime = TuiRuntime()
    runtime.set_active_renderable(
        FragmentBlock(((ASSISTANT_PREFIX_CLASS, "• first\n  second"),)),
        kind="assistant",
    )

    with patch.object(
        runtime.document,
        "fragments",
        wraps=runtime.document.fragments,
    ) as render:
        assert runtime.screen._transcript_line_prefix(0, 1)
        assert runtime.screen._transcript_line_prefix(1, 1)
        assert render.call_count == 1

        runtime.set_active_renderable(
            FragmentBlock(((ASSISTANT_PREFIX_CLASS, "• changed"),)),
            kind="assistant",
        )

        assert runtime.screen._transcript_line_prefix(0, 1)
        assert render.call_count == 2


def test_formatted_stream_wrap_releases_complete_display_rows() -> None:
    rows = wrap_formatted_lines(
        [("bold", "abcdefgh"), ("italic", "中文")],
        width=5,
    )

    assert [fragments_text(row) for row in rows] == ["abcde", "fgh中", "文"]
    assert rows[0] == [("bold", "abcde")]
    assert rows[-1] == [("italic", "文")]


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
async def test_segment_completion_keeps_rendered_markdown_stable() -> None:
    runtime = TuiRuntime()
    output = TuiOutputControl("", runtime=runtime, animate=False)
    content = TuiContentSink(output)

    with patch(
        "mind_app.tui.adapters.markdown.render_tui_markdown",
        wraps=render_tui_markdown,
    ) as render:
        await content.emit(AssistantTextDelta("**bold**"))
        await content.emit(AssistantSegmentCompleted())

        render.assert_not_called()
        assert _document_text(runtime.document) == "• bold"
        first_fragments = runtime.document.active_block.fragments
        assert any("bold" in style for style, text in first_fragments if text)

        await content.emit(AssistantTextDelta(" and `code`"))
        render.assert_not_called()
        assert _document_text(runtime.document) == "• bold"

        await content.emit(AssistantOutputBoundary())
        render.assert_called_once_with(
            "**bold**\n and `code`",
            hyperlinks=runtime.hyperlinks_enabled,
            width=max(1, runtime.terminal_width - 2),
        )

    fragments = runtime.document.blocks[-1].display_block.fragments
    assert "".join(text for _style, text in fragments) == "• bold\n  and code"
    assert any("bold" in style and text == "bold" for style, text in fragments)


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
        "mind_app.tui.adapters.markdown.render_tui_markdown",
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
