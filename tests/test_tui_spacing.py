# -*- coding: utf-8 -*-

import asyncio
import difflib
import io
import threading
import typing
from contextlib import asynccontextmanager
from copy import deepcopy
from pathlib import Path
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

from mind_app.approval.coordinator import ApprovalCoordinator
from mind_app.approval.models import ApprovalDecisionValue
from agent.ports.presentation import ApplicationView
from mind_app.presentation.terminal.capabilities import (
    TerminalCapabilities,
    TerminalColorLevel,
    TerminalIdentity,
    TerminalKind,
    TerminalTheme,
)
from infrastructure.skills import SkillSpec
from metadata import const
from mind_app.interaction.contracts import PromptContext
from mind_app.presentation.output.content import (
    AssistantOutputBoundary,
    AssistantSegmentCompleted,
    AssistantTextDelta,
    ResponseIdentity,
    SourcesOutput,
)

from mind_app.presentation.approval_views import build_approval_view
from mind_app.presentation.batch_views import (
    build_batch_completed_view,
    build_batch_start_view,
)
from mind_app.presentation import code_highlight
from mind_app.presentation.lifecycle_views import build_failure_view
from agent.application.views import (
    NativeToolResultView,
    PatchView,
    PlanItemView,
    PlanUpdateView,
    RunCompletedView,
    ToolStartView,
)
from mind_app.presentation.terminal_text import sanitize_terminal_text
from mind_app.presentation.tool_views import (
    build_generic_tool_result_view,
    build_native_tool_result_view,
    build_tool_start_view,
)
from frontends.tui.adapters.application import TuiApplicationSink
from frontends.tui.adapters.content import TuiContentSink
from frontends.tui.adapters import markdown as tui_markdown
from frontends.tui.adapters import output as tui_output_module
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
    TranscriptBacktrackRequest,
)
from frontends.tui.core.keymap import TuiRuntimeKeymap
from frontends.tui.core.hyperlinks import (
    OSC8_CLOSE,
    decorate_scrollback_hyperlinks,
    terminal_hyperlink_from_style,
    terminal_hyperlink_style
)
from frontends.tui.core.queued import TuiQueuedMessages, TuiSubmission
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
from frontends.tui.features.processes import (
    exec_session_user_shell_block,
    exec_session_summary_block,
)
from frontends.tui.core.screen import (
    FrameGeometry,
    _clear_terminal_for_resize_replay,
    _erase_terminal_scrollback,
    _set_alternate_scroll_mode,
    _set_synchronized_output,
)
from frontends.tui.core.styles import (
    ASSISTANT_PREFIX_CLASS,
    assistant_block,
    failure_parts,
    query_block,
)
from frontends.tui.rendering.separators import final_message_separator

RESPONSE_IDENTITY = ResponseIdentity("turn_test", 1, 1, 1)


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


def _rendered_screen_text(screen) -> str:
    """返回渲染屏幕中的逐行文本。"""
    return "\n".join(
        "".join(
            cells[column].char
            for column in sorted(cells)
        ).rstrip()
        for _row, cells in sorted(screen.data_buffer.items())
    )


def _first_nonblank_screen_row(screen) -> int:
    """返回渲染屏幕首个包含可见文本的行号。"""
    return min(
        row
        for row, cells in screen.data_buffer.items()
        if "".join(
            cells[column].char
            for column in sorted(cells)
        ).strip()
    )


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


async def _wait_for_scrollback_advance(
    runtime: TuiRuntime,
    *,
    after: int = 0,
) -> int:
    """等待原生滚屏游标推进且当前提交任务结束。"""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + 1.0

    while loop.time() < deadline:
        cursor = runtime.document.scrollback_line_count
        if cursor > after and runtime.viewport.scrollback_task is None:
            return cursor
        await asyncio.sleep(0.001)

    raise AssertionError("scrollback did not advance")


async def _wait_for_scrollback_settlement(runtime: TuiRuntime) -> None:
    """等待待处理的原生滚屏事务完成或确认无需提交。"""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + 1.0

    while loop.time() < deadline:
        if (
            runtime.viewport.scrollback_task is None
            and runtime.viewport._scrollback_render_revision is None
        ):
            return None
        await asyncio.sleep(0.001)

    raise AssertionError("scrollback did not settle")


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
async def test_resize_recomputes_current_natural_canvas_height() -> None:
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
            content_gap = positions[
                runtime.screen.bottom_pane_top_inset.content
            ]
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


@pytest.mark.anyio
@pytest.mark.parametrize("rows", (6, 8, 12))
@pytest.mark.parametrize(
    ("kind", "block"),
    (
        (
            "operation",
            _block("Shell ping -t 8.8.8.8\nreply"),
        ),
        (
            "system",
            FragmentBlock(
                (("class:rule", "─ Finished in 17s "),),
                line_fill=LineFill(character="─"),
            ),
        ),
        ("user", query_block("next question")),
    ),
)
async def test_scrolled_tail_keeps_bottom_pane_boundary_spacing(
    rows: int,
    kind: TuiBlockKind,
    block: FragmentBlock,
) -> None:
    with create_pipe_input() as input_obj:
        output = _KnownInlineHeightOutput(
            columns=48,
            rows=rows,
            available_rows=rows,
        )
        runtime = TuiRuntime(input_obj=input_obj, output_obj=output)
        await runtime.open()
        try:
            runtime.append_block(block, kind=kind)
            runtime.set_process_status_label("ping -t 8.8.8.8")

            await _render_next_frame(runtime)
            await _wait_for_scrollback_advance(runtime)
            screen = await _render_next_frame(runtime)
            positions = screen.visible_windows_to_write_positions

            assert not runtime.document.has_visible_content
            assert runtime.document.has_display_tail
            assert runtime.document.display_tail_kind == kind

            outer_inset = positions[
                runtime.screen.bottom_pane_top_inset.content
            ]
            process_status = positions[runtime.screen.process_status_window]
            interaction_gap_window = (
                runtime.screen.status_interaction_gap.content
            )
            top_padding = positions[runtime.screen.input_top_padding]

            assert outer_inset.ypos == 0
            assert process_status.ypos == (
                outer_inset.ypos + outer_inset.height
            )
            if runtime.screen._status_interaction_gap_visible():
                interaction_gap = positions[interaction_gap_window]
                assert interaction_gap.ypos == (
                    process_status.ypos + process_status.height
                )
                assert top_padding.ypos == (
                    interaction_gap.ypos + interaction_gap.height
                )
            else:
                assert interaction_gap_window not in positions
                assert top_padding.ypos == (
                    process_status.ypos + process_status.height
                )
        finally:
            await runtime.close()


@pytest.mark.anyio
async def test_scrolled_tail_separates_shared_activity_status_stack() -> None:
    with create_pipe_input() as input_obj:
        output = _KnownInlineHeightOutput(
            columns=48,
            rows=12,
            available_rows=12,
        )
        runtime = TuiRuntime(input_obj=input_obj, output_obj=output)
        await runtime.open()
        try:
            runtime.append_block(_block("Finished"), kind="system")
            runtime.screen.set_activity_renderable(_block("Thinking"))
            runtime.set_process_status_label("ping -t 8.8.8.8")

            await _render_next_frame(runtime)
            await _wait_for_scrollback_advance(runtime)
            screen = await _render_next_frame(runtime)
            positions = screen.visible_windows_to_write_positions

            outer_inset = positions[
                runtime.screen.bottom_pane_top_inset.content
            ]
            activity_status = positions[runtime.screen.status_window]
            interaction_gap = positions[
                runtime.screen.status_interaction_gap.content
            ]

            assert outer_inset.ypos == 0
            assert activity_status.ypos == (
                outer_inset.ypos + outer_inset.height
            )
            assert runtime.screen.process_status_window not in positions
            assert interaction_gap.ypos == (
                activity_status.ypos + activity_status.height
            )
        finally:
            await runtime.close()


async def _prepare_scrolled_turn_footer(
    runtime: TuiRuntime,
    output: _KnownInlineHeightOutput,
) -> None:
    """构造稳定正文已经完整进入原生滚屏的轮次尾部。"""
    original_print_text = runtime.screen.application.print_text

    def print_at_settled_cursor(value) -> None:
        original_print_text(value)
        output.available_rows = 7

    with patch.object(
        runtime.screen.application,
        "print_text",
        side_effect=print_at_settled_cursor,
    ):
        runtime.append_block(
            _block("\n".join(
                f"first answer {index}" for index in range(35)
            )),
            kind="assistant",
        )
        scrollback_cursor = await _wait_for_scrollback_advance(runtime)

    runtime.append_block(
        FragmentBlock(
            (("class:rule", "─ Finished in 36s "),),
            line_fill=LineFill(character="─"),
        ),
        kind="system",
    )
    screen = await _render_next_frame(runtime)
    assert "Finished in 36s" in _rendered_screen_text(screen)
    await _wait_for_scrollback_advance(
        runtime,
        after=scrollback_cursor,
    )


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
async def test_inline_process_keeps_requested_spacing_before_title(
    gap_before: int | None,
    expected_gap: str,
) -> None:
    runtime = TuiRuntime()
    runtime.append_block(_block("Finished"), kind="system")

    process = runtime.begin_inline_process(
        "exec_shell",
        _block("Exec running"),
        gap_before=gap_before,
    )

    assert _document_text(runtime.document) == f"Finished{expected_gap}Exec running"

    runtime.update_inline_process(
        _block("Exec updated"),
        session_id="exec_shell",
        gap_before=gap_before,
    )
    assert _document_text(runtime.document) == f"Finished{expected_gap}Exec updated"

    runtime.resolve_inline_process("done", session_id="exec_shell")
    assert await process == "done"
    runtime.commit_inline_process(
        _block("Exec complete"),
        session_id="exec_shell",
    )

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


def test_scrollback_prefix_includes_every_complete_stable_block() -> None:
    runtime = TuiRuntime()
    runtime.append_block(_block("prior 0\nprior 1"), kind="assistant")
    runtime.append_block(_block("new query"), kind="user")

    line_count = runtime.viewport._scrollback_prefix_line_count()

    assert line_count == 6
    assert fragments_text(
        runtime.document.scrollback_prefix_fragments(line_count)
    ) == "prior 0\nprior 1\n\n \nnew query\n "


@pytest.mark.anyio
async def test_short_stable_block_commits_on_tall_terminal() -> None:
    with create_pipe_input() as pipe_input:
        runtime = TuiRuntime(input_obj=pipe_input, output_obj=DummyOutput())

        with patch.object(
            runtime.screen.application.output,
            "get_size",
            return_value=Size(rows=24, columns=80),
        ):
            await runtime.open()
            try:
                with patch.object(
                    runtime.screen.application,
                    "print_text",
                    wraps=runtime.screen.application.print_text,
                ) as print_text:
                    runtime.append_block(
                        _block("short history"),
                        kind="assistant",
                    )
                    await _render_next_frame(runtime)
                    await _wait_for_scrollback_advance(runtime)

                assert runtime.document.scrollback_line_count == (
                    runtime.document.stable_line_count
                )
                assert fragments_text(print_text.call_args.args[0]) == (
                    "short history\n"
                )
            finally:
                await runtime.close()


@pytest.mark.anyio
async def test_completion_and_input_do_not_defer_stable_history() -> None:
    with create_pipe_input() as pipe_input:
        runtime = TuiRuntime(input_obj=pipe_input, output_obj=DummyOutput())

        with patch.object(
            runtime.screen.application.output,
            "get_size",
            return_value=Size(rows=24, columns=80),
        ):
            await runtime.open()
            try:
                pipe_input.send_text("/")
                await _wait_for_input_text(runtime, "/")
                assert runtime.screen._completion_visible()

                runtime.append_block(
                    _block("background notice"),
                    kind="notice",
                )
                await _render_next_frame(runtime)
                await _wait_for_scrollback_advance(runtime)

                assert runtime.screen.input.buffer.text == "/"
                assert runtime.screen._completion_visible()
                assert runtime.document.scrollback_line_count == (
                    runtime.document.stable_line_count
                )
            finally:
                await runtime.close()


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
            "frontends.tui.core.screen._erase_terminal_scrollback"
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
    monkeypatch.setattr("frontends.tui.core.screen.sys.platform", "win32")

    assert _set_alternate_scroll_mode(output, True) is True
    assert _set_alternate_scroll_mode(output, False) is True

    assert output.write_raw.call_args_list == [
        call("\x1b[?1007h"),
        call("\x1b[?1007l"),
    ]


def test_nested_synchronized_output_toggles_only_at_outer_boundary() -> None:
    runtime = TuiRuntime()

    with patch(
        "frontends.tui.core.screen._set_synchronized_output",
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
    monkeypatch.setattr("frontends.tui.core.screen.sys.platform", "win32")

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
async def test_dynamic_transcript_expands_known_inline_viewport() -> None:
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

            assert runtime.screen.terminal_height == 18
            assert runtime.screen._visible_height() == 18
            assert expanded_screen.height == 18
        finally:
            await runtime.close()


@pytest.mark.anyio
async def test_reply_wait_preserves_turn_boundary_through_activity_frames() -> None:
    with create_pipe_input() as pipe_input:
        output = _KnownInlineHeightOutput(
            columns=80,
            rows=18,
            available_rows=18,
        )
        runtime = TuiRuntime(input_obj=pipe_input, output_obj=output)

        await runtime.open()
        try:
            await _prepare_scrolled_turn_footer(runtime, output)

            with patch.object(
                runtime.screen.application,
                "print_text",
                wraps=runtime.screen.application.print_text,
            ) as print_text:
                runtime.submissions.enqueue_message("second question")
                assert await runtime.read_message(
                    PromptContext(model="test")
                ) == "second question"
                runtime.set_execution_active(True)
                query_screen = await _render_next_frame(runtime)

                await runtime.begin_wait_status()
                thinking_screen = await _render_next_frame(runtime)
                repeated_screen = await _render_next_frame(runtime)

            query_text = _rendered_screen_text(query_screen)
            printed = "".join(
                fragments_text(call_args.args[0])
                for call_args in print_text.call_args_list
            )
            assert "Finished in 36s" in _transcript_text(runtime.document)
            assert "second question" in f"{printed}\n{query_text}"

            for screen in (thinking_screen, repeated_screen):
                frame_text = _rendered_screen_text(screen)
                combined = f"{printed}\n{frame_text}"
                assert "Finished in 36s" in _transcript_text(runtime.document)
                assert "second question" in combined
                assert "Thinking" in frame_text
            assert print_text.called
        finally:
            await runtime.activity.clear()
            runtime.set_execution_active(False)
            await runtime.close()


@pytest.mark.anyio
@pytest.mark.parametrize("animate", (False, True))
async def test_markdown_stream_retires_stable_prefix_while_running(
    animate: bool,
) -> None:
    with create_pipe_input() as pipe_input:
        terminal = _KnownInlineHeightOutput(
            columns=80,
            rows=18,
            available_rows=18,
        )
        runtime = TuiRuntime(input_obj=pipe_input, output_obj=terminal)
        output = TuiOutputControl("", runtime=runtime, animate=animate)

        await runtime.open()
        try:
            await _prepare_scrolled_turn_footer(runtime, terminal)
            initial_scrollback = runtime.document.scrollback_line_count

            with patch.object(
                runtime.screen.application,
                "print_text",
                wraps=runtime.screen.application.print_text,
            ) as print_text:
                runtime.submissions.enqueue_message("second question")
                assert await runtime.read_message(
                    PromptContext(model="test")
                ) == "second question"
                runtime.set_execution_active(True)
                await runtime.begin_wait_status()

                source = ""
                for index in range(16):
                    content_options = (
                        f"Paragraph {index} with **bold** text.",
                        f"- list item {index}.1\n- list item {index}.2",
                        f"> quoted section {index}",
                        "```python\n"
                        f"value_{index} = items[index]\n"
                        "```",
                    )
                    content = content_options[index % len(content_options)]
                    chunk = f"## Section {index}\n\n{content}\n\n"
                    source += chunk
                    await output.append_assistant_delta(chunk)
                    await _render_next_frame(runtime)

                await output.settle_stream()
                await asyncio.sleep(
                    runtime.viewport.STREAM_SCROLLBACK_DEBOUNCE_SEC + 0.05
                )
                await _wait_for_scrollback_settlement(runtime)

            printed = "".join(
                fragments_text(call_args.args[0])
                for call_args in print_text.call_args_list
            )

            assert runtime.document.active_stream_continuation
            assert runtime.document.scrollback_line_count > initial_scrollback
            assert "Section 0" in printed

            cells = [
                cell
                for cell in runtime.document.blocks
                if cell.kind == "assistant" and cell.raw_text is not None
            ]
            streamed_source = "".join(
                str(cell.raw_text or "") for cell in cells
            ) + str(runtime.document.active_raw_text or "")
            assert streamed_source == source

            await output.stop()
            final_cells = [
                cell
                for cell in runtime.document.blocks
                if cell.kind == "assistant" and cell.raw_text is not None
            ]
            assert "".join(
                str(cell.raw_text or "") for cell in final_cells
            ).endswith(source)
            stream_cells = final_cells
            assert not stream_cells[0].stream_continuation
            assert all(
                cell.stream_continuation
                for cell in stream_cells[1:]
            )
            assert all(cell.source_renderer for cell in stream_cells)

            stream_document = TuiDocument()
            stream_document.replace_blocks(stream_cells)
            expected = tui_markdown.render_tui_assistant_markdown(
                source,
                width=80,
            )
            assert fragments_text(
                stream_document.all_fragments(width=80)
            ) == fragments_text(expected.fragments)
            assert fragments_text(
                stream_document.transcript_fragments(width=80)
            ) == fragments_text(expected.fragments)

            resized_expected = tui_markdown.render_tui_assistant_markdown(
                source,
                width=40,
            )
            assert fragments_text(
                stream_document.all_fragments(width=40)
            ) == fragments_text(resized_expected.fragments)

            transcript = _transcript_text(runtime.document)
            assert transcript.count("Section 0") == 1
            assert transcript.count("Section 15") == 1

            resized = fragments_text(
                runtime.document.all_fragments(width=40)
            )
            assert resized.count("Section 0") == 1
            assert resized.count("Section 15") == 1
            assert resized.count("• ") == 1
        finally:
            await runtime.activity.clear()
            runtime.set_execution_active(False)
            await runtime.close()


@pytest.mark.anyio
async def test_single_stable_stream_row_retires_before_viewport_batch() -> None:
    with create_pipe_input() as pipe_input:
        runtime = TuiRuntime(input_obj=pipe_input, output_obj=DummyOutput())
        output = TuiOutputControl("", runtime=runtime, animate=False)
        source = "## Stable heading\n\n"

        await runtime.open()
        try:
            await output.append_assistant_delta(source)

            assert output._stream_committed_source_end == len(source)
            assert runtime.document.blocks
            assert runtime.document.blocks[-1].raw_text == source
        finally:
            await runtime.close()


@pytest.mark.anyio
async def test_reference_stream_commits_only_parser_safe_prefix() -> None:
    with create_pipe_input() as pipe_input:
        runtime = TuiRuntime(input_obj=pipe_input, output_obj=DummyOutput())
        output = TuiOutputControl("", runtime=runtime, animate=False)
        initial = (
            "## Safe 0\n"
            "## Safe 1\n"
            "## Safe 2\n"
            "## [docs][reference]\n\n"
            "following paragraph\n"
        )
        definition = "\n[reference]: https://example.com/reference\n"

        await runtime.open()
        try:
            runtime.set_execution_active(True)
            await output.append_assistant_delta(initial)

            assert runtime.document.active_stream_continuation
            committed_source = "".join(
                str(cell.raw_text or "")
                for cell in runtime.document.blocks
                if cell.kind == "assistant" and cell.raw_text is not None
            )
            assert committed_source == (
                "## Safe 0\n## Safe 1\n## Safe 2\n"
            )
            assert "[docs][reference]" in str(
                runtime.document.active_raw_text or ""
            )

            await output.append_assistant_delta(definition)
            active = runtime.document.active_block

            assert active is not None
            assert any(
                "underline" in style
                for style, text in active.fragments
                if text == "docs"
            )

            source = initial + definition
            await output.stop()
            cells = [
                cell
                for cell in runtime.document.blocks
                if cell.kind == "assistant" and cell.raw_text is not None
            ]
            assert "".join(
                str(cell.raw_text or "") for cell in cells
            ) == source

            document = TuiDocument()
            document.replace_blocks(cells)
            expected = tui_markdown.render_tui_assistant_markdown(
                source,
                width=80,
            )
            assert fragments_text(
                document.all_fragments(width=80)
            ) == fragments_text(expected.fragments)
        finally:
            runtime.set_execution_active(False)
            await runtime.close()


@pytest.mark.anyio
@pytest.mark.parametrize("remaining_rows", (2, 4, 8))
async def test_next_assistant_stream_remains_visible_after_native_scrollback(
    remaining_rows: int,
) -> None:
    with create_pipe_input() as pipe_input:
        output = _KnownInlineHeightOutput(
            columns=80,
            rows=18,
            available_rows=18,
        )
        runtime = TuiRuntime(input_obj=pipe_input, output_obj=output)

        await runtime.open()
        try:
            original_print_text = runtime.screen.application.print_text

            def print_at_terminal_bottom(value) -> None:
                original_print_text(value)
                output.available_rows = remaining_rows

            with patch.object(
                runtime.screen.application,
                "print_text",
                side_effect=print_at_terminal_bottom,
            ):
                runtime.append_block(
                    _block("\n".join(
                        f"first answer {index}" for index in range(30)
                    )),
                    kind="assistant",
                )

                await _wait_for_scrollback_advance(runtime)

            settled_screen = await _render_next_frame(runtime)
            settled_height = settled_screen.height

            assert runtime.document.scrollback_line_count > 0
            assert output.available_rows <= settled_height < output.size.rows
            assert (
                runtime.screen.application.renderer.rows_above_layout
                == output.size.rows - settled_height
            )

            runtime.set_execution_active(True)
            runtime.append_block(query_block("second question"), kind="user")

            screen_heights = []
            transcript_rows = []
            for line_count in (1, 4, 8, 16):
                runtime.set_active_renderable(
                    _block("\n".join(
                        f"second answer {index}"
                        for index in range(line_count)
                    )),
                    kind="assistant",
                )
                screen = await _render_next_frame(runtime)
                transcript = screen.visible_windows_to_write_positions.get(
                    runtime.screen.transcript_window
                )

                assert transcript is not None
                screen_heights.append(screen.height)
                transcript_rows.append(
                    output.size.rows - screen.height
                    + transcript.ypos
                )

            assert screen_heights[0] > settled_height
            assert all(
                1 <= height <= output.size.rows
                for height in screen_heights
            )
            assert screen_heights[-1] == output.size.rows
            assert all(0 <= row < output.size.rows for row in transcript_rows)
            assert transcript_rows[-1] == 0
        finally:
            runtime.set_execution_active(False)
            await runtime.close()


@pytest.mark.anyio
async def test_repeated_assistant_streams_recover_after_scrollback() -> None:
    with create_pipe_input() as pipe_input:
        output = _KnownInlineHeightOutput(
            columns=80,
            rows=18,
            available_rows=18,
        )
        runtime = TuiRuntime(input_obj=pipe_input, output_obj=output)
        printed_batches = []

        await runtime.open()
        try:
            original_print_text = runtime.screen.application.print_text

            def print_at_terminal_bottom(value) -> None:
                printed_batches.append(fragments_text(value))
                original_print_text(value)
                output.available_rows = 4

            with patch.object(
                runtime.screen.application,
                "print_text",
                side_effect=print_at_terminal_bottom,
            ):
                runtime.append_block(
                    _block("\n".join(
                        f"first answer {index}" for index in range(30)
                    )),
                    kind="assistant",
                )

                scrollback_cursor = await _wait_for_scrollback_advance(runtime)

                for turn in (2, 3):
                    settled_screen = await _render_next_frame(runtime)
                    assert (
                        runtime.screen.application.renderer.rows_above_layout
                        == output.size.rows - settled_screen.height
                    )
                    assert 1 <= settled_screen.height <= output.size.rows

                    runtime.set_execution_active(True)
                    runtime.append_block(
                        query_block(f"question {turn}"),
                        kind="user",
                    )

                    screen_heights = []
                    final_block = None
                    for line_count in (1, 8, 16):
                        final_block = _block("\n".join(
                            f"turn {turn} line {index}"
                            for index in range(line_count)
                        ))
                        runtime.set_active_renderable(
                            final_block,
                            kind="assistant",
                        )
                        screen = await _render_next_frame(runtime)
                        screen_heights.append(screen.height)

                    assert final_block is not None
                    assert screen_heights[0] >= settled_screen.height
                    assert all(
                        1 <= height <= output.size.rows
                        for height in screen_heights
                    )
                    assert screen_heights[-1] == output.size.rows

                    runtime.commit_active_renderable(final_block)
                    runtime.set_execution_active(False)

                    await _wait_for_scrollback_settlement(runtime)
                    scrollback_cursor = runtime.document.scrollback_line_count
                    assert scrollback_cursor == runtime.document.stable_line_count

            assert len(printed_batches) >= 3
            for turn in (2, 3):
                assert sum(
                    batch.count(f"question {turn}")
                    for batch in printed_batches
                ) == 1
                final_line = f"turn {turn} line 15"
                assert sum(
                    batch.count(final_line)
                    for batch in printed_batches
                ) == 1
            assert runtime.document.scrollback_line_count == (
                runtime.document.stable_line_count
            )
        finally:
            runtime.set_execution_active(False)
            await runtime.close()


@pytest.mark.anyio
async def test_long_stream_grows_after_approval_and_consecutive_tools() -> None:
    with create_pipe_input() as pipe_input:
        terminal = _KnownInlineHeightOutput(
            columns=64,
            rows=18,
            available_rows=18,
        )
        runtime = TuiRuntime(input_obj=pipe_input, output_obj=terminal)
        output = TuiOutputControl("", runtime=runtime, animate=False)
        presentation = TuiPresentationSink(output)
        printed_batches = []

        await runtime.open()
        approval_task = None
        try:
            original_print_text = runtime.screen.application.print_text

            def print_at_terminal_bottom(value) -> None:
                printed_batches.append(fragments_text(value))
                original_print_text(value)
                terminal.available_rows = 4

            with patch.object(
                runtime.screen.application,
                "print_text",
                side_effect=print_at_terminal_bottom,
            ):
                runtime.append_block(
                    query_block("run approved tools"),
                    kind="user",
                )
                runtime.set_execution_active(True)
                await runtime.begin_wait_status()

                approval = {
                    "tool": "shell_command",
                    "command": "echo approved",
                    "arguments": {"command": "echo approved"},
                    "show_timer": False,
                }
                approval_task = asyncio.create_task(
                    ApprovalCoordinator(runtime).request(approval)
                )

                for _ in range(20):
                    await asyncio.sleep(0)
                    if runtime.screen.approval.state is not None:
                        break

                assert runtime.screen.approval.state is not None
                approval_screen = await _render_next_frame(runtime)
                approval_positions = (
                    approval_screen.visible_windows_to_write_positions
                )
                assert runtime.screen.approval_window in approval_positions
                assert runtime.screen.input.window not in approval_positions

                runtime.screen.approval.finish("accept")
                assert await approval_task == "accept"
                approval_task = None
                await runtime.end_activity_status("wait", settle=False)

                await presentation.emit(build_approval_view(
                    approval,
                    decision="accept",
                ))

                for index in range(3):
                    call_id = f"tool-{index}"
                    arguments = {"command": f"echo {call_id}"}

                    await presentation.emit(build_tool_start_view(
                        "shell_command",
                        arguments,
                        call_id=call_id,
                    ))
                    start_screen = await _render_next_frame(runtime)
                    assert runtime.screen.input.window in (
                        start_screen.visible_windows_to_write_positions
                    )

                    await presentation.emit(build_native_tool_result_view(
                        "shell_command",
                        arguments,
                        ok=True,
                        data={
                            "command": arguments["command"],
                            "output_lines": [
                                f"tool {index} output {line}"
                                for line in range(6)
                            ],
                        },
                        call_id=call_id,
                    ))
                    result_screen = await _render_next_frame(runtime)
                    result_positions = (
                        result_screen.visible_windows_to_write_positions
                    )
                    assert runtime.screen.input.window in result_positions
                    assert runtime.screen.approval_window not in result_positions

                first_source = "\n".join(
                    f"first long answer {index}" for index in range(30)
                )
                await output.append_assistant_delta(first_source + "\n")
                first_stream_screen = await _render_next_frame(runtime)
                assert first_stream_screen.height == terminal.size.rows

                await output.prepare_external_output()
                await _render_next_frame(runtime)
                runtime.set_execution_active(False)
                await _wait_for_scrollback_advance(runtime)

                settled_screen = await _render_next_frame(runtime)
                assert settled_screen.height == 5
                assert (
                    runtime.screen.application.renderer.rows_above_layout
                    == 13
                )
                assert not runtime.document.visible_stable_lines()

                assert printed_batches
                printed = "".join(printed_batches)
                assert "You approved" in printed
                for index in range(3):
                    assert f"echo tool-{index}" in printed
                assert printed.count("first long answer 29") == 1

                runtime.set_execution_active(True)
                runtime.append_block(
                    query_block("write another long answer"),
                    kind="user",
                )

                screen_heights = []
                transcript_rows = []
                previous_line_count = 0
                for line_count in (1, 4, 8, 16):
                    delta = "\n".join(
                        f"second long answer {index}"
                        for index in range(previous_line_count, line_count)
                    ) + "\n"
                    await output.append_assistant_delta(delta)
                    screen = await _render_next_frame(runtime)
                    transcript = (
                        screen.visible_windows_to_write_positions[
                            runtime.screen.transcript_window
                        ]
                    )

                    screen_heights.append(screen.height)
                    transcript_rows.append(
                        terminal.size.rows - screen.height + transcript.ypos
                    )
                    previous_line_count = line_count

                assert all(
                    1 <= height <= terminal.size.rows
                    for height in screen_heights
                )
                assert screen_heights[-1] == terminal.size.rows
                assert all(
                    0 <= row < terminal.size.rows
                    for row in transcript_rows
                )
                assert transcript_rows[-1] == 0
                assert runtime.document.active_kind == "assistant"
                assert not runtime.screen.approval.active
                assert runtime.screen.activity_block is None
        finally:
            if runtime.screen.approval.active:
                runtime.screen.approval.finish("decline")
            if approval_task is not None:
                await approval_task
            runtime.set_execution_active(False)
            await runtime.activity.clear()
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
        process = None
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
                block = exec_session_user_shell_block(
                    snapshot,
                    terminal_width=80,
                )
                if process is None:
                    process = runtime.begin_inline_process(
                        "exec_shell",
                        block,
                    )
                else:
                    runtime.update_inline_process(
                        block,
                        session_id="exec_shell",
                    )

                screen = await _render_next_frame(runtime)
                positions = screen.visible_windows_to_write_positions
                input_rows.append(
                    positions[runtime.screen.input.window].ypos
                )
                screen_heights.append(screen.height)

                assert runtime.screen._bottom_pane_top_inset_visible()
                visible = _rendered_screen_text(screen)
                assert "Running ping -t 8.8.8.8" in visible
                assert f"reply {line_count - 1}" in visible

            assert input_rows == [4, 5, 7]
            assert screen_heights == [7, 8, 10]
        finally:
            if runtime.inline_process_session_id:
                runtime.resolve_inline_process("detach", session_id="exec_shell")
                if process is not None:
                    await process
                runtime.dismiss_inline_process("exec_shell")
            await runtime.close()


@pytest.mark.anyio
async def test_inline_shell_crops_title_only_after_cell_exceeds_terminal() -> None:
    with create_pipe_input() as pipe_input:
        output = _KnownInlineHeightOutput(
            columns=80,
            rows=12,
            available_rows=6,
        )
        runtime = TuiRuntime(input_obj=pipe_input, output_obj=output)

        await runtime.open()
        process = None
        try:
            oversized = exec_session_user_shell_block(
                {
                    "ok": True,
                    "session_id": "exec_shell",
                    "command": "ping -t 8.8.8.8",
                    "status": "running",
                    "origin": "tui_shell",
                    "output_lines": [f"reply {index}" for index in range(20)],
                },
                terminal_width=80,
            )
            process = runtime.begin_inline_process("exec_shell", oversized)
            oversized_screen = await _render_next_frame(runtime)
            oversized_text = _rendered_screen_text(oversized_screen)

            assert oversized_screen.height == 12
            assert "Running ping -t 8.8.8.8" not in oversized_text
            assert "reply 19" in oversized_text

            compact = exec_session_user_shell_block(
                {
                    "ok": True,
                    "session_id": "exec_shell",
                    "command": "ping -t 8.8.8.8",
                    "status": "running",
                    "origin": "tui_shell",
                    "output_lines": ["reply 0", "reply 1"],
                },
                terminal_width=80,
            )
            runtime.update_inline_process(compact, session_id="exec_shell")
            compact_screen = await _render_next_frame(runtime)
            compact_text = _rendered_screen_text(compact_screen)

            assert "Running ping -t 8.8.8.8" in compact_text
            assert "reply 1" in compact_text
        finally:
            if runtime.inline_process_session_id:
                runtime.resolve_inline_process("detach", session_id="exec_shell")
                if process is not None:
                    await process
                runtime.dismiss_inline_process("exec_shell")
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
                notice = (
                    "Earlier messages are available — press Ctrl+T "
                    "to view the full transcript"
                )
                assert notice in printed.replace("\n", "")
                assert printed.replace("\n", "").count(notice) == 1
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
                assert notice in replayed.replace("\n", "")
                assert replayed.replace("\n", "").count(notice) == 1
                assert len(resized_main_text.splitlines()) <= 7
                assert notice not in transcript
            finally:
                await runtime.close()


@pytest.mark.anyio
async def test_restored_scrollback_expands_canvas_for_slash_completion() -> None:
    with create_pipe_input() as pipe_input:
        output = _KnownInlineHeightOutput(
            columns=40,
            rows=18,
            available_rows=7,
        )
        runtime = TuiRuntime(input_obj=pipe_input, output_obj=output)
        runtime.configure_scrollback_reflow_line_limit(1)
        restored = tuple(
            TranscriptBlock(
                display_block=_block(f"history {index:02d}"),
                transcript_block=_block(f"history {index:02d}"),
                kind="assistant",
            )
            for index in range(20)
        )

        await runtime.open()
        try:
            with patch.object(
                runtime.screen.application,
                "print_text",
            ) as print_text:
                runtime.replace_transcript(restored)
            await _wait_for_scrollback_settlement(runtime)

            printed = "".join(
                text
                for call_args in print_text.call_args_list
                for _style, text in call_args.args[0]
            )
            assert "Earlier messages are available" not in printed

            before = await _render_next_frame(runtime)
            renderer = runtime.screen.application.renderer
            before_position = before.visible_windows_to_write_positions[
                runtime.screen.input.window
            ]
            before_rows_above = renderer.rows_above_layout
            before_input_row = before_rows_above + before_position.ypos

            assert runtime.document.scrollback_line_count > 0
            assert before_rows_above > 0

            pipe_input.send_text("/")
            await _wait_for_input_text(runtime, "/")
            opened = await _render_next_frame(runtime)
            opened_position = opened.visible_windows_to_write_positions[
                runtime.screen.input.window
            ]
            opened_input_row = renderer.rows_above_layout + opened_position.ypos

            assert runtime.screen._completion_section_height() == (
                runtime.screen.COMPLETION_MAX_HEIGHT
            )
            assert opened.height > before.height
            assert renderer.rows_above_layout < before_rows_above
            assert opened_input_row < before_input_row

            runtime.input_model.dismiss_completion_menu(
                runtime.screen.input.buffer
            )
            dismissed = await _render_next_frame(runtime)
            dismissed_position = dismissed.visible_windows_to_write_positions[
                runtime.screen.input.window
            ]

            assert runtime.screen._completion_section_height() == 0
            assert dismissed_position.ypos == before_position.ypos
            assert renderer.rows_above_layout <= before_rows_above
            assert runtime.screen._visible_height() == (
                runtime.screen._natural_visible_height()
            )
        finally:
            await runtime.close()


def _spacing_test_skill(name: str) -> SkillSpec:
    """创建布局测试使用的 skill 描述。"""
    entry = Path(f"{name}/SKILL.md")
    return SkillSpec(
        name=name,
        description=f"Use {name}",
        source="test",
        root=entry.parent,
        entry=entry,
    )


@pytest.mark.anyio
async def test_restored_history_notice_prints_without_retiring_tail() -> None:
    with create_pipe_input() as pipe_input:
        output = _KnownInlineHeightOutput(
            columns=80,
            rows=24,
            available_rows=20,
        )
        runtime = TuiRuntime(input_obj=pipe_input, output_obj=output)
        runtime.configure_scrollback_reflow_line_limit(7)
        restored = tuple(
            TranscriptBlock(
                display_block=_block(f"history {index:02d}"),
                transcript_block=_block(f"history {index:02d}"),
                kind="assistant",
            )
            for index in range(8)
        )

        await runtime.open()
        try:
            with patch.object(
                runtime.screen.application,
                "print_text",
            ) as print_text:
                runtime.replace_transcript(restored)
                await _wait_for_scrollback_settlement(runtime)

            printed = "".join(
                text
                for call_args in print_text.call_args_list
                for _style, text in call_args.args[0]
            )
            compact = printed.replace("\n", "")
            assert compact.startswith(
                "Earlier messages are available — press Ctrl+T "
                "to view the full transcript"
            )
            for index in range(5, 8):
                assert compact.count(f"history {index:02d}") == 1
            assert runtime.document.scrollback_line_count == (
                runtime.document.stable_line_count
            )
            assert "history 00" not in _document_text(runtime.document)
            assert "history 07" not in _document_text(runtime.document)
        finally:
            await runtime.close()


@pytest.mark.anyio
@pytest.mark.parametrize("trigger", ("/", "$"))
async def test_restored_history_notice_keeps_completion_anchor(
    trigger: str,
) -> None:
    with create_pipe_input() as pipe_input:
        output = _KnownInlineHeightOutput(
            columns=40,
            rows=18,
            available_rows=7,
        )
        runtime = TuiRuntime(input_obj=pipe_input, output_obj=output)
        runtime.configure_scrollback_reflow_line_limit(7)
        runtime.input_model.set_skills((
            _spacing_test_skill("alpha"),
            _spacing_test_skill("beta"),
        ))
        restored = tuple(
            TranscriptBlock(
                display_block=_block(f"history {index:02d}"),
                transcript_block=_block(f"history {index:02d}"),
                kind="assistant",
            )
            for index in range(20)
        )

        await runtime.open()
        try:
            runtime.replace_transcript(restored)
            await _wait_for_scrollback_settlement(runtime)

            before = await _render_next_frame(runtime)
            renderer = runtime.screen.application.renderer
            before_position = before.visible_windows_to_write_positions[
                runtime.screen.input.window
            ]
            before_rows_above = renderer.rows_above_layout
            before_input_row = renderer.rows_above_layout + before_position.ypos

            pipe_input.send_text(trigger)
            for _ in range(100):
                await asyncio.sleep(0.002)
                state = runtime.screen.input.buffer.complete_state
                if state is not None and state.completions:
                    break
            else:
                raise AssertionError("completion did not become ready")

            opened = await _render_next_frame(runtime)
            opened_position = opened.visible_windows_to_write_positions[
                runtime.screen.input.window
            ]
            opened_input_row = renderer.rows_above_layout + opened_position.ypos

            assert runtime.screen._completion_section_height() > 0
            assert opened.height >= before.height
            assert renderer.rows_above_layout <= before_rows_above
            assert opened_input_row <= before_input_row

            runtime.input_model.dismiss_completion_menu(
                runtime.screen.input.buffer
            )
            dismissed = await _render_next_frame(runtime)
            dismissed_position = dismissed.visible_windows_to_write_positions[
                runtime.screen.input.window
            ]

            assert runtime.screen._completion_section_height() == 0
            assert (
                renderer.rows_above_layout + dismissed_position.ypos
                == opened_input_row
            )
            assert runtime.screen.canvas_spacer not in (
                dismissed.visible_windows_to_write_positions
            )
        finally:
            await runtime.close()


@pytest.mark.anyio
async def test_foreground_state_defers_scrollback_until_idle() -> None:
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
                    runtime.set_foreground_active(True)
                    for index in range(6):
                        runtime.append_block(
                            _block(f"block {index}\n" + "line\n" * 3),
                            kind="operation",
                        )

                    await asyncio.sleep(0.02)
                    assert runtime.document.scrollback_line_count == 0
                    assert not print_text.called

                    runtime.set_foreground_active(False)
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
@pytest.mark.parametrize("block_kind", get_args(TuiBlockKind))
async def test_execution_pushes_every_stable_block_kind_to_scrollback(
    block_kind: TuiBlockKind,
) -> None:
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
                    runtime.set_execution_active(True)
                    runtime.screen.set_activity_renderable(_block("Thinking"))
                    runtime.append_block(
                        _block("\n".join(
                            f"{block_kind} line {index}" for index in range(30)
                        )),
                        kind=block_kind,
                    )
                    await _render_next_frame(runtime)
                    await _wait_for_scrollback_advance(runtime)
                    screen = await _render_next_frame(runtime)

                printed = "".join(
                    text
                    for call_args in print_text.call_args_list
                    for _style, text in call_args.args[0]
                )
                positions = screen.visible_windows_to_write_positions
                bottom_windows = (
                    runtime.screen.input_top_padding,
                    runtime.screen.input.window,
                    runtime.screen.input_bottom_padding,
                    runtime.screen.footer_window,
                )
                bottom_positions = [
                    positions[window]
                    for window in bottom_windows
                    if window in positions
                ]
                status_position = positions[runtime.screen.status_window]

                assert f"{block_kind} line 0" in printed
                assert f"{block_kind} line 29" in printed
                assert runtime.document.scrollback_line_count > 0
                assert runtime.screen.canvas_spacer not in positions
                assert (
                    status_position.ypos + status_position.height
                    <= bottom_positions[0].ypos
                )
                assert all(
                    current.ypos + current.height == following.ypos
                    for current, following in zip(
                        bottom_positions,
                        bottom_positions[1:],
                    )
                )
                assert (
                    bottom_positions[-1].ypos + bottom_positions[-1].height
                    <= 10
                )
            finally:
                runtime.screen.clear_activity_renderable()
                runtime.set_execution_active(False)
                await runtime.close()


@pytest.mark.anyio
async def test_scrollback_synchronizes_the_complete_terminal_transition() -> None:
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
                "frontends.tui.core.viewport.in_terminal",
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
                    "begin",
                    "wait",
                    "acquired",
                    "print",
                    "redraw",
                    "end",
                ]
                assert len(printed) == 1
                assert printed[0].endswith("line 29\n")
            finally:
                await runtime.close()


@pytest.mark.anyio
async def test_scrollback_vt_transaction_wraps_erase_and_output() -> None:
    stream = io.StringIO()
    output = Vt100_Output(
        stream,
        lambda: Size(rows=8, columns=40),
        term="xterm-256color",
        enable_cpr=False,
    )

    with create_pipe_input() as pipe_input:
        runtime = TuiRuntime(input_obj=pipe_input, output_obj=output)
        await runtime.open()
        try:
            stream.seek(0)
            stream.truncate(0)

            runtime.append_block(
                _block("\n".join(f"line {index}" for index in range(30))),
                kind="assistant",
            )

            for _ in range(100):
                await asyncio.sleep(0.002)
                if (
                    runtime.document.scrollback_line_count > 0
                    and runtime.viewport.scrollback_task is None
                ):
                    break

            payload = stream.getvalue()
            synchronized_begin = payload.find("\x1b[?2026h")
            synchronized_end = payload.find(
                "\x1b[?2026l",
                synchronized_begin + 1,
            )
            erased = payload.find("\x1b[J", synchronized_begin)
            printed = payload.find("line 0", synchronized_begin)

            assert runtime.document.scrollback_line_count > 0
            assert -1 not in (
                erased,
                synchronized_begin,
                printed,
                synchronized_end,
            )
            assert synchronized_begin < erased < printed < synchronized_end
        finally:
            await runtime.close()


@pytest.mark.anyio
async def test_scrollback_releases_sync_when_terminal_wait_is_cancelled() -> None:
    with create_pipe_input() as pipe_input:
        runtime = TuiRuntime(input_obj=pipe_input, output_obj=DummyOutput())
        waiting = asyncio.Event()
        blocked = asyncio.Event()
        events: list[str] = []

        @asynccontextmanager
        async def blocked_terminal(render_cli_done: bool = False):
            _ = render_cli_done
            events.append("wait")
            waiting.set()
            await blocked.wait()
            yield

        with (
            patch.object(
                runtime.screen.application.output,
                "get_size",
                return_value=Size(rows=8, columns=40),
            ),
            patch(
                "frontends.tui.core.viewport.in_terminal",
                blocked_terminal,
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
                    ) as print_text,
                ):
                    runtime.append_block(
                        _block("\n".join(
                            f"line {index}" for index in range(30)
                        )),
                        kind="assistant",
                    )
                    await asyncio.wait_for(waiting.wait(), timeout=1.0)

                    runtime.set_foreground_active(True)
                    await runtime.viewport._cancel_scrollback_task()

                begin.assert_called_once_with()
                end.assert_called_once_with()
                print_text.assert_not_called()
                assert events == ["begin", "wait", "end"]
                assert runtime.document.scrollback_line_count == 0
            finally:
                runtime.set_foreground_active(False)
                await runtime.close()


@pytest.mark.anyio
async def test_scrollback_releases_sync_after_print_failure() -> None:
    with create_pipe_input() as pipe_input:
        runtime = TuiRuntime(input_obj=pipe_input, output_obj=DummyOutput())
        released = asyncio.Event()

        with patch.object(
            runtime.screen.application.output,
            "get_size",
            return_value=Size(rows=8, columns=40),
        ):
            await runtime.open()
            try:
                with (
                    patch.object(
                        runtime.screen,
                        "begin_synchronized_output",
                        return_value=True,
                    ) as begin,
                    patch.object(
                        runtime.screen,
                        "end_synchronized_output",
                        side_effect=released.set,
                    ) as end,
                    patch.object(
                        runtime.screen.application,
                        "print_text",
                        side_effect=RuntimeError("print failed"),
                    ),
                ):
                    runtime.append_block(
                        _block("\n".join(
                            f"line {index}" for index in range(30)
                        )),
                        kind="assistant",
                    )

                    await asyncio.wait_for(released.wait(), timeout=1.0)

                    runtime.set_execution_active(True)
                    await runtime.viewport._cancel_scrollback_task()

                assert begin.call_count == end.call_count >= 1
                assert runtime.document.scrollback_line_count == 0
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
async def test_scrollback_candidate_ignores_active_cell_changes() -> None:
    with create_pipe_input() as pipe_input:
        runtime = TuiRuntime(input_obj=pipe_input, output_obj=DummyOutput())
        printed: list[str] = []
        query = _block("\n".join(f"query {index}" for index in range(20)))

        @asynccontextmanager
        async def update_active_while_waiting(render_cli_done: bool = False):
            _ = render_cli_done
            runtime.set_active_renderable(
                _block("streaming response"),
                kind="assistant",
            )
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
                        "frontends.tui.core.viewport.in_terminal",
                        update_active_while_waiting,
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
                assert "query 0" in printed[0]
                visible = _document_text(runtime.document)
                assert "prior 29" not in visible
                assert "query 0" not in visible
                assert "streaming response" in visible
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
                        "frontends.tui.core.viewport.in_terminal",
                        resize_while_waiting,
                    ),
                    patch.object(
                        runtime.viewport,
                        "_schedule_scrollback_reflow",
                    ) as schedule_reflow,
                    patch.object(
                        runtime.screen,
                        "begin_synchronized_output",
                        return_value=True,
                    ) as begin,
                    patch.object(
                        runtime.screen,
                        "end_synchronized_output",
                    ) as end,
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
                begin.assert_called_once_with()
                end.assert_called_once_with()
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
                "frontends.tui.core.viewport.in_terminal",
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
                        "frontends.tui.adapters.markdown.render_tui_markdown",
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


def test_assistant_markdown_wraps_plain_paragraphs_before_prefixing() -> None:
    source = (
        "从量化指标来看，候选项集中在 app-qa（102 个文件，资产最多）、"
        "ncc-desktop-testing（SKILL.md 16.7KB，最全面，包含 cases）和 "
        "ntcpc-desktop-testing（结构最全面，包含 reports）。接下来阅读这几位候选人的 "
        "SKILL.md 正文，以评估契约质量。"
    )

    rendered_lines = {}
    for width in (48, 32):
        lines = fragments_text(
            render_tui_assistant_markdown(source, width=width).fragments
        ).splitlines()

        assert lines[0].startswith("• ")
        assert len(lines) > 1
        assert all(line.startswith("  ") for line in lines[1:])
        assert all(get_cwidth(line) <= width for line in lines)
        rendered_lines[width] = lines

    assert len(rendered_lines[32]) >= len(rendered_lines[48])


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


def test_streaming_markdown_exposes_only_source_compatible_stable_prefix() -> None:
    renderer = TuiMarkdownStreamRenderer()

    renderer.render("# one\nparagraph", width=40)
    source_len, block = renderer.stable_prefix()

    assert source_len == len("# one\n")
    assert fragments_text(block.fragments) == "# one"

    renderer.reset()
    renderer.render(
        "```markdown\n| A | B |\n|---|---|\n| 1 | 2 |\n```",
        width=40,
    )

    assert renderer.stable_prefix() == (0, FragmentBlock(()))


@pytest.mark.parametrize(
    ("source", "stable_source"),
    (
        (
            "## [docs](https://example.com)\nparagraph",
            "## [docs](https://example.com)\n",
        ),
        ("## `[items[index]]`\nparagraph", "## `[items[index]]`\n"),
        ("## \\[literal] text\nparagraph", "## \\[literal] text\n"),
        (
            "```python\nvalue = items[index]\n```\nparagraph",
            "```python\nvalue = items[index]\n```\n",
        ),
        (
            "```\n[reference]: https://example.com\n```\nparagraph",
            "```\n[reference]: https://example.com\n```\n",
        ),
    ),
)
def test_streaming_markdown_parser_allows_reference_safe_syntax(
    source: str,
    stable_source: str,
) -> None:
    renderer = TuiMarkdownStreamRenderer()

    renderer.render(source, width=40)
    source_len, _block = renderer.stable_prefix()

    assert source_len == len(stable_source)


@pytest.mark.parametrize(
    "reference_source",
    (
        "## [docs][reference]\n\nfollowing paragraph\n",
        "## ![preview][image]\n\nfollowing paragraph\n",
        "- [x] task\n\nfollowing paragraph\n",
    ),
)
def test_streaming_markdown_keeps_reference_syntax_mutable(
    reference_source: str,
) -> None:
    renderer = TuiMarkdownStreamRenderer()

    renderer.render(reference_source, width=40)

    assert renderer.stable_prefix() == (0, FragmentBlock(()))


def test_reference_definition_preserves_prior_committable_prefix() -> None:
    renderer = TuiMarkdownStreamRenderer()
    initial = (
        "## safe heading\n\n"
        "## [docs][reference]\n\n"
        "following paragraph\n"
    )

    renderer.render(initial, width=40)
    initial_end, initial_block = renderer.stable_prefix()

    assert initial_end == len("## safe heading\n\n")
    assert fragments_text(initial_block.fragments) == "## safe heading"

    extended = initial + "\n[reference]: https://example.com/reference\n"
    rendered = renderer.render(extended, width=40)
    resolved_end, resolved_block = renderer.stable_prefix()

    assert resolved_end == initial_end
    assert resolved_block == initial_block
    assert "[docs][reference]" not in fragments_text(rendered.fragments)
    assert any(
        "underline" in style
        for style, text in rendered.fragments
        if text == "docs"
    )


def test_final_markdown_makes_remaining_reference_source_committable() -> None:
    renderer = TuiMarkdownStreamRenderer()
    source = "## [docs][reference]\n\nfollowing paragraph\n"

    renderer.render(source, width=40)
    assert renderer.stable_prefix() == (0, FragmentBlock(()))

    renderer.render(source, final=True, width=40)
    source_len, block = renderer.stable_prefix()

    assert source_len == len(source)
    assert "[docs][reference]" in fragments_text(block.fragments)


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


@pytest.mark.parametrize(
    ("language", "source_lines"),
    (
        (
            "python",
            ('value = """first\n', "second\n", 'third"""\n', "print(value)\n"),
        ),
        (
            "python",
            ('"""module docs\n', "continued docs\n", 'end docs"""\n'),
        ),
        (
            "python",
            ("\n", "\n", "first = 1\n", "\n", "second = 2\n"),
        ),
        (
            "javascript",
            ("const value = `first\n", "second ${1 + 2}\n", "third`;\n"),
        ),
        (
            "rust",
            ("/* first\n", "second\n", "third */\n", "let value = 3;\n"),
        ),
        (
            "html",
            ("<section>\n", "<!-- first\n", "second -->\n", "</section>\n"),
        ),
    ),
)
def test_streaming_open_code_fence_matches_canonical_highlighting_per_line(
    language: str,
    source_lines: tuple[str, ...],
) -> None:
    renderer = TuiMarkdownStreamRenderer()
    source = f"```{language}\n"

    renderer.render(source, width=80)
    for source_line in source_lines:
        source += source_line
        streamed = renderer.render(source, width=80)
        canonical = render_tui_markdown(source, width=80)

        assert streamed.fragments == canonical.fragments


def test_streaming_open_code_fence_lexes_only_appended_complete_lines() -> None:
    renderer = TuiMarkdownStreamRenderer()
    source = "```python\n"
    renderer.render(source, width=80)

    with patch.object(
        code_highlight,
        "_lex_regex_suffix",
        wraps=code_highlight._lex_regex_suffix,
    ) as lex_suffix:
        source += "first = 1\n"
        renderer.render(source, width=80)
        source += "second = first + 1\n"
        renderer.render(source, width=80)

    assert [item.args[1] for item in lex_suffix.call_args_list] == [
        "first = 1\n",
        "second = first + 1\n",
    ]

    closed_source = source + "```\n"
    closed = renderer.render(closed_source, width=80)
    canonical = render_tui_markdown(closed_source, width=80)

    assert closed.fragments == canonical.fragments


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

                assert runtime.document.scrollback_line_count == (
                    runtime.document.stable_line_count
                )
                assert _document_text(runtime.document) == ""
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

                assert runtime.document.scrollback_line_count == (
                    runtime.document.stable_line_count
                )
                visible = _document_text(runtime.document)
                assert "first 29" not in visible
                assert "second 29" not in visible
                assert visible == ""
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
                    await _render_next_frame(runtime)
                    await _wait_for_scrollback_advance(runtime)

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
                assert runtime.screen._visible_height() == (
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
async def test_width_resize_reflow_waits_for_full_screen_overlay() -> None:
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

                runtime.toggle_transcript_overlay()

                with patch.object(
                    runtime.screen,
                    "clear_terminal_for_resize_replay",
                ) as clear:
                    terminal_size = Size(rows=8, columns=24)
                    runtime.viewport.observe_terminal_geometry(24, 8)
                    await asyncio.sleep(0.12)
                    clear.assert_not_called()

                    runtime.toggle_transcript_overlay()
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
async def test_resize_reflow_vt_transaction_wraps_erase_and_replay() -> None:
    stream = io.StringIO()
    terminal_size = Size(rows=8, columns=40)
    output = Vt100_Output(
        stream,
        lambda: terminal_size,
        term="xterm-256color",
        enable_cpr=False,
    )

    with create_pipe_input() as pipe_input:
        runtime = TuiRuntime(input_obj=pipe_input, output_obj=output)
        await runtime.open()
        try:
            runtime.append_block(
                _block("\n".join(f"entry {index:02d}" for index in range(60))),
                kind="assistant",
            )
            for _ in range(100):
                await asyncio.sleep(0.002)
                if (
                    runtime.document.scrollback_line_count > 0
                    and runtime.viewport.scrollback_task is None
                ):
                    break

            assert runtime.document.scrollback_line_count > 0
            stream.seek(0)
            stream.truncate(0)

            terminal_size = Size(rows=8, columns=24)
            runtime.viewport.observe_terminal_geometry(24, 8)
            for _ in range(200):
                await asyncio.sleep(0.002)
                if (
                    runtime.viewport._reflowed_geometry == (24, 8)
                    and runtime.viewport._scrollback_reflow_task is None
                ):
                    break

            payload = stream.getvalue()
            synchronized_begin = payload.find("\x1b[?2026h")
            synchronized_end = payload.find(
                "\x1b[?2026l",
                synchronized_begin + 1,
            )
            erased = payload.find("\x1b[J", synchronized_begin)
            cleared = payload.find(
                "\x1b[r\x1b[0m\x1b[H\x1b[2J\x1b[3J\x1b[H",
                synchronized_begin,
            )
            replayed = payload.find("entry 00", synchronized_begin)

            assert runtime.viewport._reflowed_geometry == (24, 8)
            assert -1 not in (
                synchronized_begin,
                erased,
                cleared,
                replayed,
                synchronized_end,
            )
            assert (
                synchronized_begin
                < erased
                < cleared
                < replayed
                < synchronized_end
            )
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
                        "frontends.tui.core.viewport.in_terminal",
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

                    loop = asyncio.get_running_loop()
                    deadline = loop.time() + 1.0
                    while loop.time() < deadline:
                        reflow_task = runtime.viewport._scrollback_reflow_task
                        if clear.call_count and (
                            reflow_task is None or reflow_task.done()
                        ):
                            break
                        await asyncio.sleep(0.001)

                begin.assert_called_once_with()
                clear.assert_called_once_with()
                end.assert_called_once_with()
                renderer_clear.assert_not_called()
                assert events == [
                    "begin",
                    "wait",
                    "acquired",
                    "clear",
                    "replay",
                    "redraw",
                    "end",
                ]
                assert runtime.viewport._reflowed_geometry == (36, 12)
            finally:
                await runtime.close()


@pytest.mark.anyio
async def test_resize_reflow_releases_sync_when_terminal_wait_is_cancelled(
) -> None:
    with create_pipe_input() as pipe_input:
        runtime = TuiRuntime(input_obj=pipe_input, output_obj=DummyOutput())
        terminal_size = Size(rows=8, columns=40)
        waiting = asyncio.Event()
        blocked = asyncio.Event()
        events: list[str] = []

        @asynccontextmanager
        async def blocked_terminal(render_cli_done: bool = False):
            _ = render_cli_done
            events.append("wait")
            waiting.set()
            await blocked.wait()
            yield

        with patch.object(
            runtime.screen.application.output,
            "get_size",
            side_effect=lambda: terminal_size,
        ):
            await runtime.open()
            try:
                runtime.append_block(
                    _block("\n".join(
                        f"entry {index}" for index in range(60)
                    )),
                    kind="assistant",
                )
                await asyncio.sleep(0.02)
                previous_position = runtime.document.scrollback_line_count
                assert previous_position > 0

                terminal_size = Size(rows=8, columns=24)
                runtime.viewport.observe_terminal_geometry(24, 8)
                runtime.viewport._cancel_scrollback_reflow()
                generation = runtime.viewport._reflow_generation

                with (
                    patch(
                        "frontends.tui.core.viewport.in_terminal",
                        blocked_terminal,
                    ),
                    patch.object(
                        runtime.viewport,
                        "_schedule_scrollback_reflow",
                    ),
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
                        runtime.screen,
                        "clear_terminal_for_resize_replay",
                    ) as clear,
                ):
                    task = asyncio.create_task(runtime.viewport._reflow_scrollback(
                        (24, 8),
                        generation,
                    ))
                    await asyncio.wait_for(waiting.wait(), timeout=1.0)
                    task.cancel()
                    with pytest.raises(asyncio.CancelledError):
                        await task

                begin.assert_called_once_with()
                end.assert_called_once_with()
                clear.assert_not_called()
                assert events == ["begin", "wait", "end"]
                assert runtime.document.scrollback_line_count == previous_position
                assert runtime.viewport._reflow_required is True
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
                        "frontends.tui.core.viewport.in_terminal",
                        resize_while_waiting,
                    ),
                    patch.object(
                        runtime.screen,
                        "begin_synchronized_output",
                        return_value=True,
                    ) as begin,
                    patch.object(
                        runtime.screen,
                        "end_synchronized_output",
                    ) as end,
                    patch.object(
                        runtime.screen,
                        "clear_terminal_for_resize_replay",
                    ) as clear,
                ):
                    await runtime.viewport._reflow_scrollback(
                        (24, 8),
                        generation,
                    )

                begin.assert_called_once_with()
                end.assert_called_once_with()
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
                        "frontends.tui.core.viewport.in_terminal",
                        controlled_terminal,
                    ),
                    patch.object(
                        runtime.screen,
                        "begin_synchronized_output",
                        return_value=True,
                    ) as begin,
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

                begin.assert_called_once_with()
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
                    assert runtime.screen._visible_height() == (
                        runtime.screen._natural_visible_height()
                    )

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
                assert runtime.screen._visible_height() == (
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
        terminal = SimpleNamespace(size=Size(rows=10, columns=40))

        with patch.object(
            runtime.screen.application.output,
            "get_size",
            side_effect=lambda: terminal.size,
        ):
            await runtime.open()
            try:
                async def external_program() -> str:
                    terminal.size = Size(rows=16, columns=32)
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
async def test_queued_scrollback_rechecks_foreground_state_before_flushing(
) -> None:
    with create_pipe_input() as pipe_input:
        runtime = TuiRuntime(input_obj=pipe_input, output_obj=DummyOutput())

        with patch.object(
            runtime.screen.application.output,
            "get_size",
            return_value=Size(rows=10, columns=40),
        ):
            await runtime.open()
            try:
                runtime.set_foreground_active(True)
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

                runtime.set_foreground_active(False)
                assert runtime.viewport.scrollback_task is not None
                runtime.set_foreground_active(True)

                await asyncio.sleep(0.02)

                assert runtime.document.scrollback_line_count == 0

                runtime.set_foreground_active(False)
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
                assert initial_height.min == 5
                assert initial_height.preferred == 5
                assert initial_height.max == 5

                initial_screen = runtime.screen.application.renderer.last_rendered_screen
                initial_gap = initial_screen.visible_windows_to_write_positions[
                    runtime.screen.bottom_pane_top_inset.content
                ]
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
                assert initial_gap.ypos == 0
                assert initial_top.ypos == 1
                assert initial_input.ypos == 2
                assert initial_bottom.ypos == 3
                assert initial_footer.ypos == 4
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
                active_height = screen.height

                runtime.screen.clear_activity_renderable()
                screen = await _render_next_frame(runtime)
                positions = screen.visible_windows_to_write_positions
                input_position = positions[runtime.screen.input.window]
                footer = positions[runtime.screen.footer_window]

                assert screen.height <= active_height
                assert input_position.height == 1
                assert footer.ypos + footer.height <= screen.height
            finally:
                await runtime.close()


@pytest.mark.anyio
@pytest.mark.parametrize("queue_mode", ("enter", "tab"))
async def test_activity_queue_spacing_keeps_fixed_outer_inset(
    queue_mode: str,
) -> None:
    with create_pipe_input() as pipe_input:
        runtime = TuiRuntime(input_obj=pipe_input, output_obj=DummyOutput())

        with patch.object(
            runtime.screen.application.output,
            "get_size",
            return_value=Size(rows=24, columns=80),
        ):
            await runtime.open()
            try:
                runtime.set_execution_active(True)
                runtime.screen.set_activity_renderable(_block(
                    "• Thinking (22s • esc to interrupt)"
                ))
                submission = TuiSubmission(
                    value="queued input",
                    editable_text="queued input",
                    paste_store={},
                )
                if queue_mode == "enter":
                    runtime.track_pending_steer(submission)
                    queue_label = "Messages to be submitted"
                    final_queue_line = "↳ queued input"
                else:
                    runtime.defer_submission(submission)
                    queue_label = "Queued follow-up inputs"
                    final_queue_line = "edit last queued message"

                screen = await _render_next_frame(runtime)
                positions = screen.visible_windows_to_write_positions
                rows = {
                    row: "".join(
                        cells[column].char for column in sorted(cells)
                    ).rstrip()
                    for row, cells in screen.data_buffer.items()
                }
                activity_row = next(
                    row for row, text in rows.items() if "Thinking" in text
                )
                queue_row = next(
                    row for row, text in rows.items() if queue_label in text
                )
                final_queue_row = next(
                    row
                    for row, text in rows.items()
                    if final_queue_line in text
                )
                queued = positions[runtime.screen.queued_window]
                outer_inset = positions[
                    runtime.screen.bottom_pane_top_inset.content
                ]
                top_padding = positions[runtime.screen.input_top_padding]
                input_position = positions[runtime.screen.input.window]

                assert outer_inset.ypos == 0
                assert activity_row == outer_inset.ypos + outer_inset.height
                assert queue_row == activity_row + 2
                assert not rows.get(activity_row + 1)
                assert final_queue_row == queued.ypos + queued.height - 1
                assert (
                    runtime.screen.status_interaction_gap.content
                    not in positions
                )
                assert top_padding.ypos == queued.ypos + queued.height
                assert input_position.ypos == (
                    top_padding.ypos + top_padding.height
                )
            finally:
                runtime.set_execution_active(False)
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
                content_gap = positions[
                    runtime.screen.bottom_pane_top_inset.content
                ]
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
@pytest.mark.parametrize("block_kind", get_args(TuiBlockKind))
async def test_every_transcript_kind_keeps_blank_row_before_input(
    block_kind: TuiBlockKind,
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
                runtime.append_block(_block("visible content"), kind=block_kind)
                screen = await _render_next_frame(runtime)
                positions = screen.visible_windows_to_write_positions
                transcript = positions[runtime.screen.transcript_window]
                input_position = positions[runtime.screen.input.window]
                rows = {
                    row: "".join(
                        cells[column].char for column in sorted(cells)
                    ).rstrip()
                    for row, cells in screen.data_buffer.items()
                }

                transcript_end = transcript.ypos + transcript.height

                assert runtime.document.has_display_tail
                assert input_position.ypos > transcript_end
                assert all(
                    not rows.get(row)
                    for row in range(transcript_end, input_position.ypos)
                )
            finally:
                await runtime.close()


@pytest.mark.anyio
@pytest.mark.parametrize("block_kind", get_args(TuiBlockKind))
async def test_native_scrollback_tail_keeps_content_gap_before_input(
    block_kind: TuiBlockKind,
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
                runtime.append_block(
                    _block("\n".join(f"line {index}" for index in range(30))),
                    kind=block_kind,
                )

                for _ in range(100):
                    await asyncio.sleep(0.002)
                    if runtime.document.scrollback_line_count > 0:
                        break

                screen = await _render_next_frame(runtime)
                positions = screen.visible_windows_to_write_positions
                content_gap = positions[
                    runtime.screen.bottom_pane_top_inset.content
                ]
                top_padding = positions[runtime.screen.input_top_padding]
                input_position = positions[runtime.screen.input.window]

                assert not runtime.document.has_visible_content
                assert runtime.document.has_display_tail
                assert runtime.screen._bottom_pane_top_inset_visible()
                assert content_gap.ypos == 0
                assert top_padding.ypos == (
                    content_gap.ypos + content_gap.height
                )
                assert input_position.ypos == (
                    top_padding.ypos + top_padding.height
                )
            finally:
                await runtime.close()


@pytest.mark.anyio
async def test_content_gap_preserves_natural_input_push_down() -> None:
    with create_pipe_input() as pipe_input:
        runtime = TuiRuntime(input_obj=pipe_input, output_obj=DummyOutput())

        with patch.object(
            runtime.screen.application.output,
            "get_size",
            return_value=Size(rows=24, columns=40),
        ):
            await runtime.open()
            try:
                initial = runtime.screen.application.renderer.last_rendered_screen
                initial_input = initial.visible_windows_to_write_positions[
                    runtime.screen.input.window
                ]

                runtime.append_block(_block("first"), kind="system")
                first = await _render_next_frame(runtime)
                first_input = first.visible_windows_to_write_positions[
                    runtime.screen.input.window
                ]

                runtime.append_block(_block("second"), kind="system")
                second = await _render_next_frame(runtime)
                second_input = second.visible_windows_to_write_positions[
                    runtime.screen.input.window
                ]

                assert initial_input.ypos < first_input.ypos < second_input.ypos
                assert runtime.screen._visible_height() < 24
                assert runtime.screen.canvas_spacer not in (
                    second.visible_windows_to_write_positions
                )
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
                    if "• You ran adb devices" in text
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
            process = None
            try:
                runtime.append_block(_block(">_ App (v1.0)"), kind="system")
                initial_screen = await _render_next_frame(runtime)
                assert runtime.screen.input.window in (
                    initial_screen.visible_windows_to_write_positions
                )

                snapshot = {
                    "ok": True,
                    "session_id": "exec_shell",
                    "command": "adb devices",
                    "status": "running",
                    "origin": "tui_shell",
                    "output_lines": ["List of devices attached"],
                }
                live_block = exec_session_user_shell_block(
                    snapshot,
                    terminal_width=80,
                )
                process = runtime.begin_inline_process(
                    "exec_shell",
                    live_block,
                )
                running_screen = await _render_next_frame(runtime)
                assert runtime.screen.input.window in (
                    running_screen.visible_windows_to_write_positions
                )
                assert runtime.screen.input_area.filter()
                assert runtime.screen.input_footer.filter()
                assert runtime.screen._process_status_height() == 0

                runtime.resolve_inline_process("exited", session_id="exec_shell")
                assert await process == "exited"
                process = None
                runtime.commit_inline_process(
                    exec_session_summary_block(
                        {**snapshot, "status": "exited", "exit_code": 0},
                        terminal_width=80,
                    ),
                    session_id="exec_shell",
                )
                completed_screen = await _render_next_frame(runtime)
                assert runtime.screen.input.window in (
                    completed_screen.visible_windows_to_write_positions
                )

                for screen in (
                    initial_screen,
                    running_screen,
                    completed_screen,
                ):
                    positions = screen.visible_windows_to_write_positions
                    nonblank_rows = [
                        row
                        for row, cells in screen.data_buffer.items()
                        if "".join(
                            cells[column].char
                            for column in sorted(cells)
                        ).strip()
                    ]
                    assert nonblank_rows
                    assert min(nonblank_rows) < screen.height
            finally:
                if runtime.inline_process_session_id:
                    runtime.resolve_inline_process("detach", session_id="exec_shell")
                    if process is not None:
                        await process
                    runtime.dismiss_inline_process("exec_shell")
                await runtime.close()


@pytest.mark.anyio
async def test_shell_submission_keeps_input_row_during_inline_handoff() -> None:
    with create_pipe_input() as pipe_input:
        runtime = TuiRuntime(input_obj=pipe_input, output_obj=DummyOutput())

        with patch.object(
            runtime.screen.application.output,
            "get_size",
            return_value=Size(rows=12, columns=80),
        ):
            await runtime.open()
            process = None
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
                process = runtime.begin_inline_process(
                    "exec_shell",
                    exec_session_user_shell_block(
                        snapshot,
                        terminal_width=80,
                    ),
                )
                process_screen = await _render_next_frame(runtime)
                process_input = process_screen.visible_windows_to_write_positions[
                    runtime.screen.input.window
                ]
                process_input_row = (
                    12 - runtime.screen._visible_height() + process_input.ypos
                )

                assert submitted_input_row == typed_input_row
                assert process_input_row == typed_input_row
            finally:
                if runtime.inline_process_session_id:
                    runtime.resolve_inline_process("detach", session_id="exec_shell")
                    if process is not None:
                        await process
                    runtime.dismiss_inline_process("exec_shell")
                await runtime.close()


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("value", "expected_stage_states"),
    (
        ("/resume", [False]),
        ("/does-not-exist", []),
        ("/", []),
    ),
)
async def test_inline_shell_detaches_before_next_submission_is_staged(
    value,
    expected_stage_states,
) -> None:
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
                process = runtime.begin_inline_process(
                    "exec_shell",
                    exec_session_user_shell_block(
                        {
                            "ok": True,
                            "session_id": "exec_shell",
                            "command": "ping -t 8.8.8.8",
                            "status": "running",
                            "origin": "tui_shell",
                            "output_lines": ["reply"],
                        },
                        terminal_width=80,
                    ),
                )

                async def settle_process() -> None:
                    assert await process == "detach"
                    runtime.commit_inline_process(
                        _block("• Shell ping -t 8.8.8.8\n  └ reply"),
                        session_id="exec_shell",
                    )

                settle_task = asyncio.create_task(settle_process())
                prompt_task = asyncio.create_task(runtime.read_message(
                    PromptContext(model="test")
                ))
                runtime.screen.input.buffer.text = value
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
                    stage_states.append(bool(runtime.inline_process_session_id))
                    stage_submission(*args, **kwargs)

                with patch.object(
                    runtime.document,
                    "stage_submission",
                    side_effect=stage_after_detach,
                ):
                    runtime.screen.input.buffer.validate_and_handle()
                    assert await prompt_task == value

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

                assert stage_states == expected_stage_states
                if value != "/":
                    assert submitted_input_row == active_input_row
            finally:
                if settle_task is not None:
                    settle_task.cancel()
                    await asyncio.gather(settle_task, return_exceptions=True)
                await runtime.close()


@pytest.mark.anyio
@pytest.mark.parametrize("menu_level", (2, 3, 4))
async def test_nested_menu_uses_its_own_top_padding(
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
                await _render_next_frame(runtime)

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
                menu_padding = positions[runtime.screen.menu_top_padding]

                assert menu_padding.height == 1
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
                    runtime.screen._natural_visible_height()
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

                assert result_input_row == closed_input_row
                assert result_input.ypos - result_row == 3
                assert runtime.screen.canvas_spacer not in positions
            finally:
                if runtime.screen.menu.active:
                    runtime.screen.menu.finish(None)
                if menu_task is not None:
                    await menu_task
                await runtime.close()


@pytest.mark.anyio
@pytest.mark.parametrize("terminal_rows", (1, 2, 3))
async def test_tiny_terminal_matches_bottom_pane_clipping(
    terminal_rows: int,
) -> None:
    with create_pipe_input() as pipe_input:
        runtime = TuiRuntime(input_obj=pipe_input, output_obj=DummyOutput())

        with patch.object(
            runtime.screen.application.output,
            "get_size",
            return_value=Size(rows=terminal_rows, columns=40),
        ):
            await runtime.open()
            try:
                screen = runtime.screen.application.renderer.last_rendered_screen
                positions = screen.visible_windows_to_write_positions

                assert _rendered_screen_text(screen).strip() == ""
                assert runtime.screen.input_top_padding not in positions
                assert runtime.screen.input.window not in positions
                assert runtime.screen.input_bottom_padding not in positions
                assert runtime.screen.footer_window not in positions
                assert not runtime.screen._footer_visible()
                assert runtime.screen._bottom_pane_layout().total_height == 4
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


def _assert_scrolled_input_frame_stable(
    runtime: TuiRuntime,
    screen,
    *,
    reflow_generation: int,
) -> None:
    """校验输入编辑没有被识别为终端尺寸变化。"""
    positions = screen.visible_windows_to_write_positions

    assert runtime.viewport._observed_geometry == (80, 18)
    assert runtime.viewport._reflow_generation == reflow_generation
    assert runtime.viewport._scrollback_render_revision is None
    assert runtime.viewport.scrollback_task is None
    assert runtime.viewport._scrollback_reflow_handle is None
    assert runtime.viewport._scrollback_reflow_task is None
    assert runtime.screen.input.window in positions


@pytest.mark.anyio
async def test_input_does_not_defer_scrollback() -> None:
    with create_pipe_input() as pipe_input:
        terminal = _KnownInlineHeightOutput(
            columns=80,
            rows=18,
            available_rows=18,
        )
        runtime = TuiRuntime(input_obj=pipe_input, output_obj=terminal)

        await runtime.open()
        try:
            pipe_input.send_text("draft")
            await _wait_for_input_text(runtime, "draft")
            await _render_next_frame(runtime)

            runtime.append_block(
                _block("\n".join(
                    f"answer line {index}" for index in range(35)
                )),
                kind="assistant",
            )
            await _render_next_frame(runtime)
            await _wait_for_scrollback_advance(runtime)

            assert runtime.document.scrollback_line_count == (
                runtime.document.stable_line_count
            )
            assert runtime.viewport._scrollback_render_revision is None
            assert runtime.screen.input.buffer.text == "draft"

            pipe_input.send_text("\x15")
            await _wait_for_input_text(runtime, "")

            assert runtime.viewport._scrollback_render_revision is None
        finally:
            await runtime.close()


@pytest.mark.anyio
async def test_multiline_history_toggle_after_scrollback_keeps_top_aligned(
) -> None:
    with create_pipe_input() as pipe_input:
        terminal = _KnownInlineHeightOutput(
            columns=80,
            rows=18,
            available_rows=18,
        )
        runtime = TuiRuntime(input_obj=pipe_input, output_obj=terminal)

        await runtime.open()
        try:
            await _prepare_scrolled_turn_footer(runtime, terminal)
            history = "first line\nsecond line\nthird line\nfourth line"
            runtime.input_model.history.append_string(history)
            reflow_generation = runtime.viewport._reflow_generation

            for _ in range(2):
                pipe_input.send_text("\x1b[A")
                await _wait_for_input_text(runtime, history)
                expanded = await _render_next_frame(runtime)
                _assert_scrolled_input_frame_stable(
                    runtime,
                    expanded,
                    reflow_generation=reflow_generation,
                )

                pipe_input.send_text("\x1b[B")
                await _wait_for_input_text(runtime, "")
                collapsed = await _render_next_frame(runtime)
                _assert_scrolled_input_frame_stable(
                    runtime,
                    collapsed,
                    reflow_generation=reflow_generation,
                )
        finally:
            await runtime.close()


@pytest.mark.anyio
@pytest.mark.parametrize("line_count", (3, 4, 5, 17))
async def test_multiline_paste_grows_without_hiding_input_or_padding_top(
    line_count: int,
) -> None:
    with create_pipe_input() as pipe_input:
        terminal = _KnownInlineHeightOutput(
            columns=80,
            rows=18,
            available_rows=18,
        )
        runtime = TuiRuntime(input_obj=pipe_input, output_obj=terminal)

        await runtime.open()
        try:
            await _prepare_scrolled_turn_footer(runtime, terminal)
            await _wait_for_scrollback_settlement(runtime)
            settled = await _render_next_frame(runtime)
            settled_height = settled.height
            settled_top_row = _first_nonblank_screen_row(settled)

            assert (
                runtime.screen.application.renderer.rows_above_layout
                == terminal.size.rows - settled_height
            )

            pasted = "\n".join(
                f"pasted line {index}" for index in range(line_count)
            ) + "\n"
            pipe_input.send_text(f"\x1b[200~{pasted}\x1b[201~")
            await _wait_for_input_text(runtime, pasted)

            screen = await _render_next_frame(runtime)
            positions = screen.visible_windows_to_write_positions
            input_position = positions[runtime.screen.input.window]
            render_info = runtime.screen.input.window.render_info
            expected_height = runtime.screen._natural_visible_height()

            assert screen.height == expected_height
            assert (
                runtime.screen.application.renderer.rows_above_layout
                == terminal.size.rows - expected_height
            )
            assert render_info is not None
            assert runtime.screen.canvas_spacer not in positions

            if line_count < 17:
                assert input_position.height == line_count + 1
                assert render_info.vertical_scroll == 0
                footer = positions[runtime.screen.footer_window]
                assert footer.ypos + footer.height == expected_height
            else:
                footer = positions[runtime.screen.footer_window]
                assert footer.ypos + footer.height == 18
                assert input_position.ypos + input_position.height == 16
                assert input_position.height == 14
                assert render_info.vertical_scroll == 4

            pipe_input.send_text("\x15" * (pasted.count("\n") + 1))
            await _wait_for_input_text(runtime, "")
            restored = await _render_next_frame(runtime)
            restored_positions = restored.visible_windows_to_write_positions
            restored_footer = restored_positions[runtime.screen.footer_window]

            assert runtime.screen.canvas_spacer not in restored_positions
            assert _first_nonblank_screen_row(restored) <= settled_top_row
            assert restored_footer.ypos + restored_footer.height == (
                runtime.screen._visible_height()
            )
            assert runtime.screen._visible_height() == (
                runtime.screen._natural_visible_height()
            )
        finally:
            await runtime.close()


@pytest.mark.anyio
@pytest.mark.parametrize("expansion_rows", (20, 30))
async def test_saturated_input_keeps_footer_and_scrolls_only_overflow(
    expansion_rows: int,
) -> None:
    with create_pipe_input() as pipe_input:
        terminal = _KnownInlineHeightOutput(
            columns=80,
            rows=24,
            available_rows=12,
        )
        runtime = TuiRuntime(input_obj=pipe_input, output_obj=terminal)

        await runtime.open()
        try:
            await _prepare_scrolled_turn_footer(runtime, terminal)

            pipe_input.send_text("\x0f" * expansion_rows)
            await _wait_for_input_text(runtime, "\n" * expansion_rows)
            expanded = await _render_next_frame(runtime)
            positions = expanded.visible_windows_to_write_positions
            input_position = positions[runtime.screen.input.window]
            footer_position = positions[runtime.screen.footer_window]
            render_info = runtime.screen.input.window.render_info

            assert expanded.height == terminal.size.rows
            assert input_position.height == (
                terminal.size.rows
                - runtime.screen._bottom_pane_top_inset_height()
                - runtime.screen.INPUT_SURFACE_PADDING_HEIGHT * 2
                - runtime.screen._footer_height()
            )
            assert footer_position.ypos + footer_position.height == 24
            assert render_info is not None
            assert render_info.vertical_scroll == (
                expansion_rows + 1 - input_position.height
            )
            assert runtime.screen.canvas_spacer not in positions

            pipe_input.send_text("\x15" * expansion_rows)
            await _wait_for_input_text(runtime, "")
            collapsed = await _render_next_frame(runtime)
            positions = collapsed.visible_windows_to_write_positions
            footer_position = positions[runtime.screen.footer_window]

            assert runtime.screen._input_height() == 1
            assert runtime.screen._visible_height() == (
                runtime.screen._natural_visible_height()
            )
            assert footer_position.ypos + footer_position.height == (
                runtime.screen._visible_height()
            )
            assert runtime.screen.canvas_spacer not in positions
        finally:
            await runtime.close()


@pytest.mark.anyio
async def test_multiline_submission_moves_to_scrollback() -> None:
    with create_pipe_input() as pipe_input:
        terminal = _KnownInlineHeightOutput(
            columns=80,
            rows=18,
            available_rows=18,
        )
        runtime = TuiRuntime(input_obj=pipe_input, output_obj=terminal)

        await runtime.open()
        try:
            await _prepare_scrolled_turn_footer(runtime, terminal)
            await _wait_for_scrollback_settlement(runtime)
            settled = await _render_next_frame(runtime)
            settled_top_row = _first_nonblank_screen_row(settled)
            scrollback_cursor = runtime.document.scrollback_line_count

            prompt_task = asyncio.create_task(runtime.read_message(
                PromptContext(model="test")
            ))
            pasted = "first line\nsecond line\nthird line\nfourth line"
            pipe_input.send_text(f"\x1b[200~{pasted}\x1b[201~")
            await _wait_for_input_text(runtime, pasted)
            await _render_next_frame(runtime)

            runtime.screen.input.buffer.validate_and_handle()

            assert await prompt_task == pasted
            await _wait_for_input_text(runtime, "")
            await _wait_for_scrollback_advance(
                runtime,
                after=scrollback_cursor,
            )

            submitted = await _render_next_frame(runtime)
            positions = submitted.visible_windows_to_write_positions

            assert _first_nonblank_screen_row(submitted) <= settled_top_row
            assert runtime.screen.input.window in positions
            assert "fourth line" not in _rendered_screen_text(submitted)
            assert "fourth line" in fragments_text(
                runtime.document.all_fragments(width=80)
            )
        finally:
            await runtime.close()


@pytest.mark.anyio
@pytest.mark.parametrize("queue_mode", ("pending", "follow_up"))
async def test_consecutive_multiline_queue_submissions_keep_outer_inset(
    queue_mode: str,
) -> None:
    with create_pipe_input() as pipe_input:
        terminal = _KnownInlineHeightOutput(
            columns=80,
            rows=30,
            available_rows=30,
        )
        runtime = TuiRuntime(input_obj=pipe_input, output_obj=terminal)

        await runtime.open()
        try:
            runtime.append_block(_block(">_ App (v1.0)"), kind="system")
            prompt_task = asyncio.create_task(runtime.read_message(
                PromptContext(model="test")
            ))
            first = "\n".join(f"first line {index}" for index in range(8))
            pipe_input.send_text(f"\x1b[200~{first}\x1b[201~")
            await _wait_for_input_text(runtime, first)
            await _render_next_frame(runtime)

            runtime.screen.input.buffer.validate_and_handle()
            assert await prompt_task == first
            await _wait_for_input_text(runtime, "")

            runtime.set_execution_active(True)
            runtime.screen.set_activity_renderable(_block("• Thinking · 5.9s"))

            def track_pending(submission, queue_only) -> bool:
                if queue_only:
                    runtime.defer_submission(submission)
                else:
                    runtime.track_pending_steer(submission)
                return True

            runtime.bind_turn_input_handler(track_pending)
            for submission_index in range(2):
                queued_text = "\n".join(
                    f"queued {submission_index} line {line_index}"
                    for line_index in range(8)
                )
                pipe_input.send_text(
                    f"\x1b[200~{queued_text}\x1b[201~"
                )
                await _wait_for_input_text(runtime, queued_text)
                await _render_next_frame(runtime)

                if queue_mode == "pending":
                    runtime.screen.input.buffer.validate_and_handle()
                else:
                    runtime.submissions.queue_input(
                        runtime.screen.input.buffer
                    )
                await _wait_for_input_text(runtime, "")
                queued = await _render_next_frame(runtime)
                positions = queued.visible_windows_to_write_positions

                if queue_mode == "pending":
                    assert runtime.submissions.pending_steers.active
                else:
                    assert runtime.submissions.queued_messages.active
                assert runtime.screen.canvas_spacer not in positions
                outer_inset = positions[
                    runtime.screen.bottom_pane_top_inset.content
                ]
                assert outer_inset.ypos == 0
                assert outer_inset.height == 1
                assert _first_nonblank_screen_row(queued) == 1
        finally:
            runtime.set_execution_active(False)
            await runtime.close()


@pytest.mark.anyio
async def test_single_line_queues_grow_scrolled_canvas_without_clipping_top(
) -> None:
    with create_pipe_input() as pipe_input:
        terminal = _KnownInlineHeightOutput(
            columns=80,
            rows=24,
            available_rows=24,
        )
        runtime = TuiRuntime(input_obj=pipe_input, output_obj=terminal)

        await runtime.open()
        try:
            await _prepare_scrolled_turn_footer(runtime, terminal)
            await _wait_for_scrollback_settlement(runtime)
            initial = await _render_next_frame(runtime)
            initial_rows_above = (
                runtime.screen.application.renderer.rows_above_layout
            )
            initial_top_row = _first_nonblank_screen_row(initial)

            runtime.set_execution_active(True)
            submissions = []

            def track_pending(submission, queue_only) -> bool:
                submissions.append(submission)
                if queue_only:
                    runtime.defer_submission(submission)
                else:
                    runtime.track_pending_steer(submission)
                return True

            runtime.bind_turn_input_handler(track_pending)

            pipe_input.send_text("follow-up")
            await _wait_for_input_text(runtime, "follow-up")
            pipe_input.send_text("\t")
            await _wait_for_input_text(runtime, "")
            follow_up = await _render_next_frame(runtime)
            follow_up_rows_above = (
                runtime.screen.application.renderer.rows_above_layout
            )

            pipe_input.send_text("steer")
            await _wait_for_input_text(runtime, "steer")
            pipe_input.send_text("\r")
            await _wait_for_input_text(runtime, "")
            steer = await _render_next_frame(runtime)
            steer_rows_above = (
                runtime.screen.application.renderer.rows_above_layout
            )

            assert initial.height <= follow_up.height <= steer.height
            assert (
                initial_rows_above
                >= follow_up_rows_above
                >= steer_rows_above
            )
            assert "Finished in 36s" in _transcript_text(runtime.document)

            runtime.resolve_pending_steer(submissions[-1].client_message_id)
            contracted = await _render_next_frame(runtime)
            contracted_positions = (
                contracted.visible_windows_to_write_positions
            )

            assert runtime.screen._visible_height() < steer.height
            assert _first_nonblank_screen_row(contracted) <= initial_top_row
            assert runtime.screen.canvas_spacer not in contracted_positions
            assert "Finished in 36s" in _transcript_text(runtime.document)
        finally:
            runtime.set_execution_active(False)
            await runtime.close()


@pytest.mark.anyio
async def test_ctrl_w_shrinking_input_after_scrollback_keeps_top_aligned(
) -> None:
    with create_pipe_input() as pipe_input:
        terminal = _KnownInlineHeightOutput(
            columns=80,
            rows=18,
            available_rows=18,
        )
        runtime = TuiRuntime(input_obj=pipe_input, output_obj=terminal)

        await runtime.open()
        try:
            await _prepare_scrolled_turn_footer(runtime, terminal)
            buffer = runtime.screen.input.buffer
            buffer.text = "one\ntwo\nthree\nfour"
            buffer.cursor_position = len(buffer.text)
            await _render_next_frame(runtime)
            reflow_generation = runtime.viewport._reflow_generation

            for expected in (
                "one\ntwo\nthree\n",
                "one\ntwo\n",
                "one\n",
                "",
            ):
                pipe_input.send_text("\x17")
                await _wait_for_input_text(runtime, expected)
                screen = await _render_next_frame(runtime)
                _assert_scrolled_input_frame_stable(
                    runtime,
                    screen,
                    reflow_generation=reflow_generation,
                )
        finally:
            await runtime.close()


@pytest.mark.anyio
async def test_delete_shrinking_input_after_scrollback_keeps_top_aligned(
) -> None:
    with create_pipe_input() as pipe_input:
        terminal = _KnownInlineHeightOutput(
            columns=80,
            rows=18,
            available_rows=18,
        )
        runtime = TuiRuntime(input_obj=pipe_input, output_obj=terminal)

        await runtime.open()
        try:
            await _prepare_scrolled_turn_footer(runtime, terminal)
            buffer = runtime.screen.input.buffer
            buffer.text = "a\nb\nc\nd"
            buffer.cursor_position = 0
            await _render_next_frame(runtime)
            reflow_generation = runtime.viewport._reflow_generation

            for expected in ("b\nc\nd", "c\nd", "d"):
                pipe_input.send_text("\x1b[3~\x1b[3~")
                await _wait_for_input_text(runtime, expected)
                screen = await _render_next_frame(runtime)
                _assert_scrolled_input_frame_stable(
                    runtime,
                    screen,
                    reflow_generation=reflow_generation,
                )
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
                assert runtime.screen._visible_height() == (
                    runtime.screen._natural_visible_height()
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
                assert runtime.screen._visible_height() == (
                    runtime.screen._natural_visible_height()
                )
                assert runtime.screen.canvas_spacer not in positions
            finally:
                await runtime.close()


@pytest.mark.anyio
async def test_programmatic_input_replacement_restores_natural_canvas() -> None:
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
                value = "x" + "\n" * 8
                runtime.screen.input.buffer.text = value
                runtime.screen.input.buffer.cursor_position = len(value)
                expanded = await _render_next_frame(runtime)

                assert expanded.height > initial_height
                assert runtime.screen._input_height() == 9

                runtime.replace_input_text("replacement")
                collapsed = await _render_next_frame(runtime)
                positions = collapsed.visible_windows_to_write_positions

                assert runtime.screen._input_height() == 1
                assert runtime.screen._visible_height() == (
                    runtime.screen._natural_visible_height()
                )
                assert runtime.screen._visible_height() < expanded.height
                assert runtime.screen.canvas_spacer not in positions
            finally:
                await runtime.close()


@pytest.mark.anyio
async def test_repeated_ctrl_u_clears_input_without_top_canvas_spacer() -> None:
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
                await _wait_for_scrollback_advance(runtime)
                await _render_next_frame(runtime)
                initial_height = runtime.screen._visible_height()

                value = "\n".join(f"line {index}" for index in range(8))
                runtime.screen.input.buffer.text = value
                runtime.screen.input.buffer.cursor_position = len(value)
                await _render_next_frame(runtime)

                assert runtime.screen._visible_height() > initial_height

                pipe_input.send_text("\x15" * (value.count("\n") + 1))
                await _wait_for_input_text(runtime, "")

                screen = await _render_next_frame(runtime)
                positions = screen.visible_windows_to_write_positions

                assert runtime.screen._input_height() == 1
                assert runtime.screen._visible_height() == initial_height
                assert runtime.screen._visible_height() == (
                    runtime.screen._natural_visible_height()
                )
                assert runtime.screen.canvas_spacer not in positions
            finally:
                await runtime.close()


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("expansion_rows", "terminal_rows"),
    ((10, 24), (20, 34), (20, 24)),
)
async def test_ctrl_u_restores_natural_layout_after_multiline_terminal_scroll(
    expansion_rows: int,
    terminal_rows: int,
) -> None:
    with create_pipe_input() as pipe_input:
        terminal = _KnownInlineHeightOutput(
            columns=40,
            rows=terminal_rows,
            available_rows=12,
        )
        runtime = TuiRuntime(input_obj=pipe_input, output_obj=terminal)
        output = TuiOutputControl("", runtime=runtime, animate=False)

        await runtime.open()
        try:
            runtime.set_execution_active(True)
            for index in range(8):
                await output.append_assistant_delta(f"line {index}\n")
                await _render_next_frame(runtime)

            baseline = await _render_next_frame(runtime)

            pipe_input.send_text("\x0f" * expansion_rows)
            await _wait_for_input_text(runtime, "\n" * expansion_rows)
            expanded = await _render_next_frame(runtime)
            expected_expanded_height = min(
                terminal_rows,
                baseline.height + expansion_rows,
            )
            assert expanded.height == expected_expanded_height
            assert (
                runtime.screen.application.renderer.rows_above_layout
                == terminal_rows - expected_expanded_height
            )

            pipe_input.send_text("\x15" * expansion_rows)
            await _wait_for_input_text(runtime, "")
            restored = await _render_next_frame(runtime)
            positions = restored.visible_windows_to_write_positions
            footer_position = positions[runtime.screen.footer_window]

            assert runtime.screen._input_height() == 1
            assert runtime.screen._visible_height() == (
                runtime.screen._natural_visible_height()
            )
            assert footer_position.ypos + footer_position.height == (
                runtime.screen._visible_height()
            )
            assert runtime.screen.canvas_spacer not in positions

            if expansion_rows == 20 and terminal_rows == 24:
                for index in range(5):
                    await output.append_assistant_delta(
                        f"continued line {index}\n"
                    )
                    current = await _render_next_frame(runtime)
                    current_positions = (
                        current.visible_windows_to_write_positions
                    )
                    current_footer = current_positions[
                        runtime.screen.footer_window
                    ]

                    assert runtime.screen._visible_height() == (
                        runtime.screen._natural_visible_height()
                    )
                    assert current_footer.ypos + current_footer.height == (
                        runtime.screen._visible_height()
                    )
                    assert (
                        runtime.screen.canvas_spacer
                        not in current_positions
                    )
        finally:
            runtime.set_execution_active(False)
            await output.stop()
            await runtime.close()


@pytest.mark.anyio
@pytest.mark.parametrize("attempt", range(3))
async def test_reexpanded_input_never_adds_top_canvas_spacer(
    attempt: int,
) -> None:
    _ = attempt
    with create_pipe_input() as pipe_input:
        terminal = _KnownInlineHeightOutput(
            columns=40,
            rows=24,
            available_rows=12,
        )
        runtime = TuiRuntime(input_obj=pipe_input, output_obj=terminal)

        await runtime.open()
        try:
            await _prepare_scrolled_turn_footer(runtime, terminal)

            pipe_input.send_text("\x0f" * 20)
            await _wait_for_input_text(runtime, "\n" * 20)
            expanded = await _render_next_frame(runtime)
            peak_input_height = (
                expanded.visible_windows_to_write_positions[
                    runtime.screen.input.window
                ].height
            )
            pipe_input.send_text("\x15" * 20)
            await _wait_for_input_text(runtime, "")
            collapsed = await _render_next_frame(runtime)
            collapsed_positions = (
                collapsed.visible_windows_to_write_positions
            )

            assert runtime.screen.canvas_spacer not in collapsed_positions
            assert runtime.screen._input_height() == 1
            assert runtime.screen._visible_height() == (
                runtime.screen._natural_visible_height()
            )

            for line_count in range(1, peak_input_height):
                pipe_input.send_text("\x0f")
                await _wait_for_input_text(runtime, "\n" * line_count)
                expanded = await _render_next_frame(runtime)
                positions = expanded.visible_windows_to_write_positions

                assert runtime.screen.canvas_spacer not in positions
                assert runtime.screen._input_height() == line_count + 1
                assert runtime.screen._visible_height() == (
                    runtime.screen._natural_visible_height()
                )
        finally:
            await runtime.close()


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("clear_mode", "key_sequence"),
    (
        ("ctrl_c", "\x03"),
        ("ctrl_u", "\x15"),
        ("ctrl_w", "\x17"),
        ("delete", "\x1b[3~"),
    ),
)
async def test_idle_destructive_edit_uses_current_input_height(
    clear_mode: str,
    key_sequence: str,
) -> None:
    with create_pipe_input() as pipe_input:
        terminal = _KnownInlineHeightOutput(
            columns=40,
            rows=24,
            available_rows=12,
        )
        runtime = TuiRuntime(input_obj=pipe_input, output_obj=terminal)

        await runtime.open()
        try:
            await _prepare_scrolled_turn_footer(runtime, terminal)
            assert runtime.execution_active is False

            pipe_input.send_text("\x0f" * 20)
            await _wait_for_input_text(runtime, "\n" * 20)
            expanded = await _render_next_frame(runtime)
            peak_input_height = runtime.screen._input_height()

            assert peak_input_height == (
                terminal.size.rows
                - runtime.screen._bottom_pane_top_inset_height()
                - runtime.screen.INPUT_SURFACE_PADDING_HEIGHT * 2
                - runtime.screen._footer_height()
            )
            assert expanded.height == terminal.size.rows

            if clear_mode == "delete":
                runtime.screen.input.buffer.cursor_position = 0

            remaining_rows = (
                range(19, -1, -1)
                if clear_mode in {"ctrl_u", "delete"}
                else (0,)
            )
            for remaining_rows_count in remaining_rows:
                pipe_input.send_text(key_sequence)
                await _wait_for_input_text(
                    runtime,
                    "\n" * remaining_rows_count,
                )
                collapsed = await _render_next_frame(runtime)
                positions = collapsed.visible_windows_to_write_positions
                footer = positions[runtime.screen.footer_window]

                assert runtime.screen._visible_height() == (
                    runtime.screen._natural_visible_height()
                )
                assert footer.ypos + footer.height == (
                    runtime.screen._visible_height()
                )
                assert runtime.screen.canvas_spacer not in positions
        finally:
            await runtime.close()


@pytest.mark.anyio
@pytest.mark.parametrize(
    "clear_mode",
    ("ctrl_u", "ctrl_w", "delete", "history", "undo"),
)
async def test_multiline_clear_restores_natural_layout_after_oversized_stream(
    clear_mode: str,
) -> None:
    with create_pipe_input() as pipe_input:
        runtime = TuiRuntime(input_obj=pipe_input, output_obj=DummyOutput())
        output = TuiOutputControl("", runtime=runtime, animate=False)
        presentation = TuiPresentationSink(output)

        with patch.object(
            runtime.screen.application.output,
            "get_size",
            return_value=Size(rows=16, columns=40),
        ):
            await runtime.open()
            try:
                runtime.set_execution_active(True)
                for index in range(16):
                    await output.append_assistant_delta(
                        f"## Section {index}\n\nParagraph {index}.\n\n"
                    )
                    await _render_next_frame(runtime)

                await _wait_for_scrollback_advance(runtime)
                runtime.screen.set_activity_renderable(_block("Thinking"))
                await presentation.emit(build_native_tool_result_view(
                    "js_repl",
                    {"code": "const value = 1;"},
                    ok=True,
                    data={
                        "output": "\n".join(
                            f"tool output {index}" for index in range(30)
                        ),
                    },
                    call_id="input-clear-tool",
                ))
                await _render_next_frame(runtime)
                await _wait_for_scrollback_settlement(runtime)
                settled_screen = await _render_next_frame(runtime)
                settled_height = runtime.screen._visible_height()
                settled_positions = (
                    settled_screen.visible_windows_to_write_positions
                )
                settled_footer = settled_positions[
                    runtime.screen.footer_window
                ]

                assert settled_height == (
                    runtime.screen._natural_visible_height()
                )
                assert (
                    settled_footer.ypos + settled_footer.height
                    == settled_height
                )
                assert runtime.screen.canvas_spacer not in settled_positions
                assert "tool output 29" not in _document_text(runtime.document)
                assert "tool output 29" in _transcript_text(runtime.document)

                runtime.screen.clear_activity_renderable()
                idle_screen = await _render_next_frame(runtime)
                idle_height = runtime.screen._visible_height()
                idle_natural_height = runtime.screen._natural_visible_height()
                idle_positions = idle_screen.visible_windows_to_write_positions
                idle_footer = idle_positions[runtime.screen.footer_window]

                assert idle_height == idle_natural_height
                assert idle_height < settled_height
                assert idle_footer.ypos + idle_footer.height == idle_height
                assert runtime.screen.canvas_spacer not in idle_positions

                runtime.screen.set_activity_renderable(_block("Thinking"))
                await _render_next_frame(runtime)

                pasted = "\n".join(
                    "d" if clear_mode == "delete" else f"draft {index}"
                    for index in range(8)
                )
                buffer = runtime.screen.input.buffer
                if clear_mode == "history":
                    runtime.input_model.history.append_string(pasted)
                    pipe_input.send_text("\x1b[A")
                elif clear_mode == "undo":
                    buffer.save_to_undo_stack()
                    buffer.text = pasted
                    buffer.cursor_position = len(pasted)
                else:
                    pipe_input.send_text(f"\x1b[200~{pasted}\x1b[201~")
                await _wait_for_input_text(runtime, pasted)
                await _render_next_frame(runtime)
                assert runtime.screen._input_height() > 1

                runtime.screen.clear_activity_renderable()
                if clear_mode == "ctrl_u":
                    pipe_input.send_text("\x15" * (pasted.count("\n") + 1))
                elif clear_mode == "ctrl_w":
                    pipe_input.send_text("\x17" * 16)
                elif clear_mode == "delete":
                    buffer.cursor_position = 0
                    pipe_input.send_text("\x1b[3~" * len(pasted))
                elif clear_mode == "history":
                    pipe_input.send_text("\x1b[B")
                else:
                    pipe_input.send_text("\x1a")
                await _wait_for_input_text(runtime, "")
                screen = await _render_next_frame(runtime)
                positions = screen.visible_windows_to_write_positions

                assert runtime.screen._visible_height() == (
                    runtime.screen._natural_visible_height()
                )
                footer = positions[runtime.screen.footer_window]
                assert footer.ypos + footer.height == (
                    runtime.screen._visible_height()
                )
                assert runtime.screen.canvas_spacer not in positions
            finally:
                runtime.screen.clear_activity_renderable()
                runtime.set_execution_active(False)
                await output.stop()
                await runtime.close()


@pytest.mark.anyio
async def test_folded_multiline_paste_collapses_without_top_spacer() -> None:
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
                footer = positions[runtime.screen.footer_window]

                assert expanded_height == 16
                assert runtime.screen._input_height() == 1
                assert runtime.screen._natural_visible_height() == initial_height
                assert runtime.screen._visible_height() == initial_height
                assert runtime.screen.canvas_spacer not in positions
                assert footer.ypos + footer.height == initial_height
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
                assert runtime.screen._visible_height() == (
                    runtime.screen._natural_visible_height()
                )

                pipe_input.send_text("\x0f" * 8)
                await _wait_for_input_text(runtime, "\n" * 8)

                pipe_input.send_text("\x7f" * 8)
                await _wait_for_input_text(runtime, "")

                screen = await _render_next_frame(runtime)

                assert runtime.document.scrollback_line_count > 0
                assert runtime.screen._visible_height() == (
                    runtime.screen._natural_visible_height()
                )
                assert runtime.screen.canvas_spacer not in (
                    screen.visible_windows_to_write_positions
                )
            finally:
                await runtime.close()


@pytest.mark.anyio
async def test_submission_handoff_synchronizes_native_history() -> None:
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

                    assert begin_synchronized.call_count >= 1
                    assert (
                        begin_synchronized.call_count
                        == end_synchronized.call_count
                    )

                transition = frames[start:]
                assert transition
                assert runtime.document.blocks[-1].raw_text == (
                    "first\nsecond\nthird"
                )
                assert runtime.document.scrollback_line_count == (
                    runtime.document.stable_line_count
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
async def test_approval_dismissal_restores_current_stream_layout(
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

                approval_task = asyncio.create_task(ApprovalCoordinator(runtime).request({
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
                        runtime.screen.approval.state is not None
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
                assert input_row == normal_input_row
                assert runtime.screen._visible_height() == (
                    runtime.screen._natural_visible_height()
                )
                assert runtime.screen.canvas_spacer not in positions

                runtime.set_active_renderable(
                    _block("answer 0\nanswer 1"),
                    kind="assistant",
                )
                screen = await _render_next_frame(runtime)
                positions = screen.visible_windows_to_write_positions
                assert runtime.screen._visible_height() == (
                    runtime.screen._natural_visible_height()
                )
                assert runtime.screen.canvas_spacer not in positions

                footer_position = positions[
                    runtime.screen.footer_window
                ]

                assert footer_position.ypos + footer_position.height == (
                    runtime.screen._visible_height()
                )
            finally:
                await runtime.close()


@pytest.mark.anyio
async def test_approval_replaces_composer_after_query_enters_scrollback(
) -> None:
    with create_pipe_input() as pipe_input:
        output = _AlternateScreenOutput(columns=80, rows=24)
        runtime = TuiRuntime(input_obj=pipe_input, output_obj=output)
        await runtime.open()
        approval_task = None
        try:
            with patch.object(
                runtime.screen.application,
                "print_text",
                wraps=runtime.screen.application.print_text,
            ) as print_text:
                runtime.append_block(
                    query_block("执行adb devices"),
                    kind="user",
                )
                runtime.set_execution_active(True)
                await runtime.begin_wait_status()
                await _render_next_frame(runtime)
                await _wait_for_scrollback_advance(runtime)

            query_scrollback = "".join(
                fragments_text(call_args.args[0])
                for call_args in print_text.call_args_list
            )

            approval_task = asyncio.create_task(ApprovalCoordinator(runtime).request({
                "tool": "shell_command",
                "command": "adb devices",
                "show_timer": False,
            }))

            for _ in range(100):
                await asyncio.sleep(0)
                if (
                    runtime.screen.approval.state is not None
                    and runtime.screen.activity_block is None
                ):
                    break
            else:
                raise AssertionError("approval did not settle")

            screen = await _render_next_frame(runtime)
            positions = screen.visible_windows_to_write_positions
            content_gap = positions[
                runtime.screen.bottom_pane_top_inset.content
            ]
            approval = positions[runtime.screen.approval_window]

            nonblank_rows = {
                row: "".join(
                    cells[column].char for column in sorted(cells)
                ).rstrip()
                for row, cells in screen.data_buffer.items()
            }
            question_row = next(
                row
                for row, text in nonblank_rows.items()
                if "Would you like" in text
            )

            assert "执行adb devices" not in _rendered_screen_text(screen)
            assert "执行adb devices" in _transcript_text(runtime.document)
            assert query_scrollback == " \n› 执行adb devices\n \n"
            assert approval.ypos == content_gap.ypos + content_gap.height
            assert question_row == approval.ypos + 1
            assert runtime.screen.input.window not in positions
            assert runtime.screen.footer_window not in positions
            assert runtime.screen.canvas_spacer not in positions
            assert runtime.screen._bottom_pane_top_inset_height() == 1
        finally:
            if runtime.screen.approval.state is not None:
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
async def test_markdown_hyperlink_degrades_safely_in_dynamic_tui() -> None:
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

    overlay.toggle_raw_mode()
    raw = overlay.fragments()

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
        TerminalColorLevel.TRUECOLOR,
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
    assert ("bold fg:#6EE7A8", "✓") in fragments
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


def test_transcript_overlay_search_keeps_hyperlink_metadata() -> None:
    runtime = TuiRuntime()
    runtime.append_block(
        render_tui_assistant_markdown(
            "[docs](https://example.com/docs)",
            40,
            hyperlinks=True,
        ),
        kind="assistant",
        raw_text="[docs](https://example.com/docs)",
    )
    runtime.toggle_transcript_overlay()
    overlay = runtime.screen.transcript_overlay

    overlay.begin_search()
    overlay.append_search_text("docs")
    assert overlay.confirm_search()

    linked_styles = [
        style
        for style, text in overlay.visible_fragments()
        if "docs" in text
    ]
    assert linked_styles
    assert all(
        "class:transcript.overlay.search-match" in style
        for style in linked_styles
    )
    assert {
        terminal_hyperlink_from_style(style)
        for style in linked_styles
    } == {"https://example.com/docs"}


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
@pytest.mark.parametrize("delta", ("after", "after\n", "\r\nafter"))
async def test_completed_work_inserts_exact_separator_newline_layout(
    delta: str,
) -> None:
    runtime = TuiRuntime()
    output = TuiOutputControl("", runtime=runtime, animate=False)
    presentation = TuiPresentationSink(output)

    await output.append_assistant_delta("before")
    await output.prepare_external_output()
    await presentation.emit(build_native_tool_result_view(
        "shell_command",
        {"command": "echo done"},
        ok=True,
        data={"command": "echo done", "output_lines": ["done"]},
        call_id="done",
    ))
    await output.append_assistant_delta(delta)
    await output.prepare_external_output()

    assert [item.kind for item in runtime.document.blocks] == [
        "assistant",
        "operation",
        "system",
        "assistant",
    ]
    lines = [
        fragments_text(line)
        for line in split_formatted_lines(runtime.document.fragments(width=40))
    ]
    assert lines == [
        "• before",
        "",
        "• Ran echo done",
        "  └ done",
        "",
        "─" * 40,
        "",
        "• after",
    ]


@pytest.mark.anyio
async def test_view_image_does_not_insert_work_separator() -> None:
    runtime = TuiRuntime()
    output = TuiOutputControl("", runtime=runtime, animate=False)
    presentation = TuiPresentationSink(output)

    await presentation.emit(build_generic_tool_result_view(
        "view_image",
        "C:/tmp/image.png",
        ok=True,
    ))
    await output.append_assistant_delta("after")
    await output.prepare_external_output()

    assert [item.kind for item in runtime.document.blocks] == [
        "operation",
        "assistant",
    ]
    lines = [
        fragments_text(line)
        for line in split_formatted_lines(runtime.document.fragments(width=40))
    ]
    assert lines == [
        "• Viewed",
        "  └ C:/tmp/image.png",
        "",
        "• after",
    ]


@pytest.mark.anyio
async def test_pure_assistant_turn_does_not_insert_separator() -> None:
    runtime = TuiRuntime()
    output = TuiOutputControl("", runtime=runtime, animate=False)

    await output.append_assistant_delta("before")
    await output.prepare_external_output()
    await output.append_assistant_delta("after")
    await output.prepare_external_output()

    assert [item.kind for item in runtime.document.blocks] == [
        "assistant",
        "assistant",
    ]


@pytest.mark.anyio
async def test_completed_work_adds_finished_label_only_at_turn_tail() -> None:
    runtime = TuiRuntime()
    output = TuiOutputControl("", runtime=runtime, animate=False)
    presentation = TuiPresentationSink(output)
    output._turn_started_at = 0.0

    with patch.object(tui_output_module.time, "perf_counter", return_value=61.0):
        output.note_work_activity()
        await presentation.emit(RunCompletedView(usage={}))

    assert [item.kind for item in runtime.document.blocks] == ["system"]
    rendered = fragments_text(runtime.document.fragments(width=60))
    assert "Finished in 1m 01s" in rendered
    assert get_cwidth(rendered) == 60


@pytest.mark.anyio
async def test_completed_work_tail_separator_has_exact_newline_layout() -> None:
    runtime = TuiRuntime()
    output = TuiOutputControl("", runtime=runtime, animate=False)
    presentation = TuiPresentationSink(output)

    await presentation.emit(build_native_tool_result_view(
        "shell_command",
        {"command": "echo done"},
        ok=True,
        data={"command": "echo done", "output_lines": ["done"]},
        call_id="done",
    ))
    await presentation.emit(RunCompletedView(usage={}))

    lines = [
        fragments_text(line)
        for line in split_formatted_lines(runtime.document.fragments(width=60))
    ]
    assert lines == [
        "• Ran echo done",
        "  └ done",
        "",
        "─" * 60,
    ]


def test_final_separator_uses_dim_style_and_hides_short_elapsed_label() -> None:
    short = final_message_separator(60.0)
    long = final_message_separator(61.0)

    assert fragments_text(short.fragments) == "─"
    assert fragments_text(long.fragments) == "─ Finished in 1m 01s ─"
    assert all("dim" in style for style, _text in long.fragments)


@pytest.mark.anyio
async def test_tui_shell_titles_share_one_visual_row_budget() -> None:
    runtime = TuiRuntime()
    runtime.screen._output_size = lambda: (20, 24)
    output = TuiOutputControl("", runtime=runtime, animate=False)
    presentation = TuiPresentationSink(output)
    command = "echo " + "界🙂" * 80
    arguments = {"command": command}

    await presentation.emit(build_tool_start_view(
        "shell_command",
        arguments,
        call_id="running",
    ))
    await presentation.emit(build_native_tool_result_view(
        "shell_command",
        arguments,
        ok=True,
        data={"command": command, "output_lines": ["done"]},
        call_id="ran",
    ))
    lines = [
        fragments_text(line)
        for line in split_formatted_lines(runtime.document.fragments(width=20))
    ]
    titles = tuple(
        next(line for line in lines if line.startswith(prefix))
        for prefix in ("• Running ", "• Ran ")
    )
    summaries = tuple(
        title.removeprefix(prefix)
        for title, prefix in zip(
            titles,
            ("• Running ", "• Ran "),
            strict=True,
        )
    )

    assert all(get_cwidth(title) <= 20 for title in titles)
    assert all("│" not in title for title in titles)
    assert len(set(summaries)) == 1
    assert command in _transcript_text(runtime.document)


@pytest.mark.anyio
async def test_tui_exec_lifecycle_uses_one_codex_terminal_projection() -> None:
    runtime = TuiRuntime()
    runtime.set_execution_active(True)
    await runtime.begin_wait_status()
    output = TuiOutputControl("", runtime=runtime, animate=False)
    presentation = TuiPresentationSink(output)
    command = "python -m pytest tests/test_tui_shell.py -q"

    await presentation.emit(build_native_tool_result_view(
        "exec_command",
        {"command": command},
        ok=True,
        data={
            "session_id": "session-1",
            "command": command,
            "status": "running",
        },
    ))
    await presentation.emit(build_native_tool_result_view(
        "write_stdin",
        {"session_id": "session-1", "stdin": ""},
        ok=True,
        data={
            "session_id": "session-1",
            "command": command,
            "status": "running",
            "output_lines": ["first poll output"],
        },
    ))
    activity_text = "".join(
        text for _style, text in runtime.screen.activity_block.fragments
    )
    assert activity_text.startswith("• Terminal · ")
    assert "esc to interrupt" not in activity_text
    assert f"\n  └ {command}" in activity_text
    await presentation.emit(build_native_tool_result_view(
        "write_stdin",
        {"session_id": "session-1", "stdin": ""},
        ok=True,
        data={
            "session_id": "session-1",
            "command": command,
            "status": "running",
            "output_lines": ["second poll output"],
        },
    ))
    await presentation.emit(build_native_tool_result_view(
        "write_stdin",
        {"session_id": "session-1", "stdin": "q\n"},
        ok=True,
        data={
            "session_id": "session-1",
            "command": command,
            "status": "running",
        },
    ))

    text = _document_text(runtime.document)
    assert "Started" not in text
    assert "Wrote stdin" not in text
    assert text.count("Waited for background terminal") == 1
    assert "↳ Interacted with background terminal" in text
    assert "  └ q" in text
    assert "first poll output" not in text
    assert "second poll output" not in text
    runtime.set_execution_active(False)


@pytest.mark.anyio
async def test_tui_exec_wait_flushes_before_assistant_output() -> None:
    runtime = TuiRuntime()
    runtime.set_execution_active(True)
    await runtime.begin_wait_status()
    output = TuiOutputControl("", runtime=runtime, animate=False)
    presentation = TuiPresentationSink(output)
    content = TuiContentSink(
        output,
        before_assistant_output=(
            presentation.flush_terminal_waits_before_assistant_output
        ),
    )
    command = "ping -t 8.8.8.8"

    await presentation.emit(build_native_tool_result_view(
        "exec_command",
        {"command": command},
        ok=True,
        data={
            "session_id": "session-1",
            "command": command,
            "status": "running",
        },
    ))
    await presentation.emit(build_native_tool_result_view(
        "write_stdin",
        {"session_id": "session-1", "stdin": ""},
        ok=True,
        data={
            "session_id": "session-1",
            "command": command,
            "status": "running",
        },
    ))

    assert "Waited for background terminal" not in _document_text(runtime.document)

    await content.emit(AssistantTextDelta("Streaming response.", RESPONSE_IDENTITY))
    await output.prepare_external_output()

    text = _document_text(runtime.document)
    assert text.index("Waited for background terminal") < text.index(
        "Streaming response."
    )
    runtime.set_execution_active(False)


@pytest.mark.anyio
async def test_tui_exec_wait_flushes_when_terminal_session_changes() -> None:
    runtime = TuiRuntime()
    runtime.set_execution_active(True)
    await runtime.begin_wait_status()
    output = TuiOutputControl("", runtime=runtime, animate=False)
    presentation = TuiPresentationSink(output)
    content = TuiContentSink(
        output,
        before_assistant_output=(
            presentation.flush_terminal_waits_before_assistant_output
        ),
    )

    for session_id, command in (
        ("session-1", "ping -t 8.8.8.8"),
        ("session-2", "python -m pytest -q"),
    ):
        await presentation.emit(build_native_tool_result_view(
            "write_stdin",
            {"session_id": session_id, "stdin": ""},
            ok=True,
            data={
                "session_id": session_id,
                "command": command,
                "status": "running",
            },
        ))

    text = _document_text(runtime.document)
    assert text.count("Waited for background terminal") == 1
    assert "ping -t 8.8.8.8" in text
    assert "python -m pytest -q" not in text

    await content.emit(AssistantTextDelta("Answer.", RESPONSE_IDENTITY))
    await output.prepare_external_output()

    text = _document_text(runtime.document)
    assert text.count("Waited for background terminal") == 2
    assert text.index("ping -t 8.8.8.8") < text.index("python -m pytest -q")
    assert text.index("python -m pytest -q") < text.index("Answer.")
    runtime.set_execution_active(False)


@pytest.mark.anyio
async def test_tui_exec_wait_is_ignored_when_turn_is_not_running() -> None:
    runtime = TuiRuntime()
    output = TuiOutputControl("", runtime=runtime, animate=False)
    presentation = TuiPresentationSink(output)

    await presentation.emit(build_native_tool_result_view(
        "write_stdin",
        {"session_id": "session-1", "stdin": ""},
        ok=True,
        data={
            "session_id": "session-1",
            "command": "ping -t 8.8.8.8",
            "status": "running",
        },
    ))

    assert "Waited for background terminal" not in _document_text(runtime.document)
    assert runtime.screen.activity_block is None

    runtime.set_execution_active(True)
    await presentation.emit(build_native_tool_result_view(
        "write_stdin",
        {"session_id": "missing-session", "stdin": ""},
        ok=False,
        data={
            "session_id": "missing-session",
            "status": "failed",
        },
    ))

    assert "Waited for background terminal" not in _document_text(runtime.document)
    assert runtime.screen.activity_block is None
    runtime.set_execution_active(False)


@pytest.mark.anyio
async def test_tui_controlled_write_stdin_does_not_enter_terminal_wait() -> None:
    runtime = TuiRuntime()
    output = TuiOutputControl("", runtime=runtime, animate=False)
    presentation = TuiPresentationSink(output)

    await presentation.emit(build_native_tool_result_view(
        "write_stdin",
        {
            "session_id": "session-1",
            "stdin": "",
            "control": "interrupt",
        },
        ok=True,
        data={
            "session_id": "session-1",
            "command": "ping -t 8.8.8.8",
            "status": "exited",
            "control": "interrupt",
            "output_lines": ["stopped"],
        },
    ))

    text = _document_text(runtime.document)
    assert runtime.screen.activity_block is None
    assert "Terminal · " not in text
    assert "Interacted with background terminal" in text
    assert "stopped" not in text


@pytest.mark.anyio
async def test_tui_shell_preview_keeps_each_output_on_one_visual_row() -> None:
    runtime = TuiRuntime()
    runtime.screen._output_size = lambda: (20, 24)
    output = TuiOutputControl("", runtime=runtime, animate=False)
    presentation = TuiPresentationSink(output)
    output_lines = [
        "value-" * 40,
        "界" * 120,
        "👩\u200d💻" * 60,
    ]

    await presentation.emit(build_native_tool_result_view(
        "shell_command",
        {"command": "printf output"},
        ok=True,
        data={
            "command": "printf output",
            "output_lines": output_lines,
        },
        call_id="preview-width",
    ))

    display = fragments_text(runtime.document.fragments(width=20))
    display_lines = display.splitlines()

    assert len(display_lines) == 4
    assert all(get_cwidth(line) <= 20 for line in display_lines)
    assert display_line_count(display, width=20) == len(display_lines)
    assert all(line in _transcript_text(runtime.document) for line in output_lines)


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


def _oversized_patch_text() -> str:
    return "\n".join((
        "*** Begin Patch",
        "*** Update File: sample.py",
        "@@ -1 +1,31 @@",
        "-old value",
        *(f"+new value {index}" for index in range(30)),
        "+patch-final-token",
        "*** End Patch",
    ))


def _patch_hunks(old_content: str, new_content: str):
    """构造测试用的新协议 canonical hunk。"""
    old_lines = old_content.splitlines()
    new_lines = new_content.splitlines()
    hunks = []
    matcher = difflib.SequenceMatcher(None, old_lines, new_lines, autojunk=False)
    for group in matcher.get_grouped_opcodes(n=3):
        lines = []
        for tag, old_start, old_end, new_start, new_end in group:
            if tag == "equal":
                lines.extend(
                    {
                        "kind": "context",
                        "text": old_lines[old_index],
                        "old_line": old_index + 1,
                        "new_line": new_index + 1,
                    }
                    for old_index, new_index in zip(
                        range(old_start, old_end),
                        range(new_start, new_end),
                        strict=True,
                    )
                )
            if tag in {"delete", "replace"}:
                lines.extend(
                    {
                        "kind": "remove",
                        "text": old_lines[index],
                        "old_line": index + 1,
                        "new_line": None,
                    }
                    for index in range(old_start, old_end)
                )
            if tag in {"insert", "replace"}:
                lines.extend(
                    {
                        "kind": "add",
                        "text": new_lines[index],
                        "old_line": None,
                        "new_line": index + 1,
                    }
                    for index in range(new_start, new_end)
                )
        if lines:
            hunks.append({"lines": lines})
    return hunks


def _patch_result_view(
    call_id: str,
    *,
    ok: bool = True,
    new_content: str = "new\n",
):
    patch_text = (
        "*** Begin Patch\n"
        "*** Update File: sample.py\n"
        "@@\n"
        "-old\n"
        "+new\n"
        "*** End Patch"
    )
    data = (
        {
            "files": [{
                "path": "sample.py",
                "source_path": None,
                "action": "modify",
                "added_lines": 1,
                "removed_lines": 1,
            }],
            "delta": {
                "exact": True,
                "changes": [{
                    "path": "sample.py",
                    "action": "modify",
                    "old_content": "old\n",
                    "new_content": new_content,
                    "source_path": None,
                    "hunks": _patch_hunks("old\n", new_content),
                }],
            },
        }
        if ok
        else {
            "reason": "patch_context_mismatch",
            "path": "sample.py",
            "target_line": 1,
        }
    )
    return build_native_tool_result_view(
        "apply_patch",
        {"patch": patch_text},
        ok=ok,
        data=data,
        call_id=call_id,
    )


def _patch_preview_data(*, new_content: str = "new\n"):
    """返回测试用的新协议补丁预览。"""
    return {
        "files": [{
            "path": "sample.py",
            "source_path": None,
            "action": "modify",
        }],
        "delta": {
            "exact": True,
            "changes": [{
                "path": "sample.py",
                "action": "modify",
                "old_content": "old\n",
                "new_content": new_content,
                "source_path": None,
                "hunks": _patch_hunks("old\n", new_content),
            }],
        },
    }


@pytest.mark.anyio
async def test_tui_patch_start_and_success_share_one_cell() -> None:
    runtime = TuiRuntime()
    output = TuiOutputControl("", runtime=runtime, animate=False)
    presentation = TuiPresentationSink(output)
    result = _patch_result_view("patch-one")
    preview = build_tool_start_view(
        "apply_patch",
        {"patch": result.raw_patch},
        patch_preview=_patch_preview_data(),
        call_id="patch-one",
    )

    await presentation.emit(preview)

    assert runtime.document.active_block is None
    assert len(runtime.document.blocks) == 1
    assert "• Edited sample.py" in _document_text(runtime.document)

    await presentation.emit(result)

    assert runtime.document.active_block is None
    assert len(runtime.document.blocks) == 1
    assert runtime.document.blocks[0].source is preview
    assert runtime.document.blocks[0].raw_text == result.raw_patch
    assert _document_text(runtime.document).count("• Edited sample.py") == 1
    assert "Applying patch" not in _document_text(runtime.document)


@pytest.mark.anyio
async def test_tui_patch_preview_is_stable_and_success_is_not_duplicated() -> None:
    runtime = TuiRuntime()
    output = TuiOutputControl("", runtime=runtime, animate=False)
    presentation = TuiPresentationSink(output)
    result = _patch_result_view("patch-preview")
    preview_data = _patch_preview_data()
    preview = build_tool_start_view(
        "apply_patch",
        {"patch": result.raw_patch},
        patch_preview=preview_data,
        call_id="patch-preview",
    )

    await presentation.emit(preview)

    assert runtime.document.active_block is None
    assert len(runtime.document.blocks) == 1
    assert _document_text(runtime.document).count("• Edited sample.py") == 1

    await presentation.emit(result)

    assert len(runtime.document.blocks) == 1
    assert runtime.document.blocks[0].source is preview
    assert "Applying patch" not in _document_text(runtime.document)


@pytest.mark.anyio
async def test_tui_patch_failure_appends_after_stable_preview() -> None:
    runtime = TuiRuntime()
    output = TuiOutputControl("", runtime=runtime, animate=False)
    presentation = TuiPresentationSink(output)
    result = _patch_result_view("patch-preview-failed", ok=False)
    preview_data = _patch_preview_data()
    preview = build_tool_start_view(
        "apply_patch",
        {"patch": result.raw_patch},
        patch_preview=preview_data,
        call_id="patch-preview-failed",
    )

    await presentation.emit(preview)
    await presentation.emit(result)

    assert len(runtime.document.blocks) == 2
    assert runtime.document.blocks[0].source is preview
    assert runtime.document.blocks[1].source is result
    assert _document_text(runtime.document).startswith("• Edited sample.py")
    assert "✘ Failed to apply patch" in _document_text(runtime.document)


@pytest.mark.anyio
async def test_tui_patch_failure_appends_without_replacing_proposed_cell() -> None:
    runtime = TuiRuntime()
    output = TuiOutputControl("", runtime=runtime, animate=False)
    presentation = TuiPresentationSink(output)
    result = _patch_result_view("patch-failed", ok=False)
    preview = build_tool_start_view(
        "apply_patch",
        {"patch": result.raw_patch},
        patch_preview=_patch_preview_data(),
        call_id="patch-failed",
    )

    await presentation.emit(preview)
    await presentation.emit(result)

    assert len(runtime.document.blocks) == 2
    assert runtime.document.active_block is None
    assert _document_text(runtime.document).startswith("• Edited sample.py")
    assert "✘ Failed to apply patch" in _document_text(runtime.document)
    assert "Applying patch" not in _document_text(runtime.document)


@pytest.mark.anyio
async def test_tui_patch_new_call_stabilizes_previous_cell() -> None:
    runtime = TuiRuntime()
    output = TuiOutputControl("", runtime=runtime, animate=False)
    presentation = TuiPresentationSink(output)
    first = _patch_result_view("patch-one")
    second = _patch_result_view("patch-two", new_content="second\n")
    first_preview = build_tool_start_view(
        "apply_patch",
        {"patch": first.raw_patch},
        patch_preview=_patch_preview_data(),
        call_id="patch-one",
    )
    second_preview = build_tool_start_view(
        "apply_patch",
        {"patch": second.raw_patch},
        patch_preview=_patch_preview_data(new_content="second\n"),
        call_id="patch-two",
    )

    await presentation.emit(first_preview)
    await presentation.emit(second_preview)

    assert len(runtime.document.blocks) == 2
    assert runtime.document.active_block is None
    assert [item.source.call_id for item in runtime.document.blocks] == [
        "patch-one",
        "patch-two",
    ]

    await presentation.emit(second)

    assert [item.source.call_id for item in runtime.document.blocks] == [
        "patch-one",
        "patch-two",
    ]


@pytest.mark.anyio
async def test_tui_unmatched_patch_result_does_not_overwrite_active_call() -> None:
    runtime = TuiRuntime()
    output = TuiOutputControl("", runtime=runtime, animate=False)
    presentation = TuiPresentationSink(output)
    first = _patch_result_view("patch-one")
    other = _patch_result_view("patch-other", new_content="other\n")
    first_preview = build_tool_start_view(
        "apply_patch",
        {"patch": first.raw_patch},
        patch_preview=_patch_preview_data(),
        call_id="patch-one",
    )

    await presentation.emit(first_preview)
    await presentation.emit(other)

    assert [item.source.call_id for item in runtime.document.blocks] == [
        "patch-one",
        "patch-other",
    ]

    await presentation.emit(first)

    assert [item.source.call_id for item in runtime.document.blocks] == [
        "patch-one",
        "patch-other",
    ]


@pytest.mark.anyio
async def test_tui_patch_reflows_from_structured_view_after_resize() -> None:
    runtime = TuiRuntime()
    output = TuiOutputControl("", runtime=runtime, animate=False)
    presentation = TuiPresentationSink(output)
    long_content = "界🙂value-" * 30 + "\n"
    result = _patch_result_view("patch-resize", new_content=long_content)

    await presentation.emit(result)

    narrow = fragments_text(runtime.document.fragments(width=40))
    wide = fragments_text(runtime.document.fragments(width=120))

    assert len(narrow.splitlines()) > len(wide.splitlines())
    assert all(get_cwidth(line) <= 40 for line in narrow.splitlines())
    assert all(get_cwidth(line) <= 120 for line in wide.splitlines())
    assert runtime.document.blocks[0].source is result
    assert runtime.document.blocks[0].raw_text == result.raw_patch


@pytest.mark.anyio
async def test_tui_patch_background_fills_each_wrapped_row_after_resize() -> None:
    capabilities = TerminalCapabilities(
        identity=TerminalIdentity(TerminalKind.UNKNOWN, "test"),
        color_level=TerminalColorLevel.TRUECOLOR,
        theme=TerminalTheme(background=(0, 0, 0)),
    )
    runtime = TuiRuntime(terminal_capabilities=capabilities)
    output = TuiOutputControl("", runtime=runtime, animate=False)
    presentation = TuiPresentationSink(output)
    result = _patch_result_view(
        "patch-background-resize",
        new_content=("value-" * 30) + "\n",
    )

    await presentation.emit(result)

    for width in (40, 72):
        lines = split_formatted_lines(runtime.document.fragments(width=width))
        changed = [
            line for line in lines
            if any("bg:#213A2B" in style for style, _text in line)
        ]

        assert len(changed) > 1
        assert all(get_cwidth(fragments_text(line)) == width for line in changed)
        assert all("bg:#213A2B" in line[-1][0] for line in changed)


@pytest.mark.anyio
async def test_assistant_output_sees_stable_patch_cell() -> None:
    runtime = TuiRuntime()
    output = TuiOutputControl("", runtime=runtime, animate=False)
    presentation = TuiPresentationSink(output)
    result = _patch_result_view("patch-active")
    preview = build_tool_start_view(
        "apply_patch",
        {"patch": result.raw_patch},
        patch_preview=_patch_preview_data(),
        call_id="patch-active",
    )

    await presentation.emit(preview)

    await output.prepare_external_output()

    assert runtime.document.active_block is None
    assert runtime.document.blocks[0].source is preview


@pytest.mark.anyio
async def test_tui_bounds_every_tool_block_family_and_keeps_transcript() -> None:
    runtime = TuiRuntime()
    runtime.screen._output_size = lambda: (32, 24)
    output = TuiOutputControl("", runtime=runtime, animate=False)
    presentation = TuiPresentationSink(output)
    patch_text = _oversized_patch_text()
    views_and_hidden_markers = (
        (build_tool_start_view(
            "remote_tool",
            {
                **{f"argument_{index}": index for index in range(8)},
                "nested": {"value": "generic-start-final-token"},
            },
        ), "generic-start-final-token"),
        (build_generic_tool_result_view(
            "remote_tool",
            "\n".join(f"generic result {index}" for index in range(12)),
            ok=True,
        ), "generic result 11"),
        (build_tool_start_view(
            "shell_command",
            {"command": "printf " + "value-" * 40 + "shell-final-token"},
        ), "shell-final-token"),
        (build_native_tool_result_view(
            "shell_command",
            {"command": "printf output"},
            ok=True,
            data={
                "command": "printf output",
                "output_lines": [
                    f"shell output {index}" for index in range(12)
                ],
            },
        ), "shell output 5"),
        (build_native_tool_result_view(
            "exec_command",
            {"command": "run background"},
            ok=True,
            data={
                "command": "run background",
                "status": "exited",
                "output_lines": [
                    f"exec output {index}" for index in range(12)
                ],
            },
        ), "exec output 5"),
        (build_native_tool_result_view(
            "apply_patch",
            {"patch": patch_text},
            ok=True,
            data={
                "files": [{
                    "path": "sample.py",
                    "source_path": None,
                    "action": "modify",
                    "added_lines": 31,
                    "removed_lines": 1,
                }],
                "delta": {
                    "exact": True,
                    "changes": [{
                            "path": "sample.py",
                            "action": "modify",
                            "old_content": "old value\n",
                            "new_content": "\n".join((
                            *(f"new value {index}" for index in range(30)),
                            "patch-final-token",
                                "",
                            )),
                            "source_path": None,
                            "hunks": _patch_hunks(
                                "old value\n",
                                "\n".join((
                                    *(f"new value {index}" for index in range(30)),
                                    "patch-final-token",
                                    "",
                                )),
                            ),
                        }],
                },
            },
            call_id="patch-bounds",
        ), "patch-final-token"),
        (build_tool_start_view(
            "js_repl",
            {"code": "\n".join(
                f"const value{index} = {index};" for index in range(30)
            )},
        ), "const value29 = 29;"),
        (build_native_tool_result_view(
            "js_repl",
            {"code": "const value = 1;"},
            ok=True,
            data={
                "output": "\n".join(
                    f"javascript output {index}" for index in range(30)
                ),
            },
        ), "javascript output 29"),
        (build_batch_start_view((
            ("remote_tool", {
                **{f"argument_{index}": index for index in range(8)},
                "nested": {"value": "batch-start-final-token"},
            }),
        )), "batch-start-final-token"),
        (build_batch_completed_view((
            (
                "remote_tool",
                True,
                "\n".join(f"batch output {index}" for index in range(12)),
            ),
        )), "batch output 11"),
    )

    for view, _marker in views_and_hidden_markers:
        await presentation.emit(view)

    display = _document_text(runtime.document)
    transcript = _transcript_text(runtime.document)

    assert all(
        marker not in display
        for view, marker in views_and_hidden_markers
        if not isinstance(view, PatchView)
    )
    assert "patch-final-token" in display
    assert all(
        marker in transcript
        for _view, marker in views_and_hidden_markers
    )
    assert "… +" in display
    assert "to view transcript" in display


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("rows", "columns"),
    ((8, 20), (12, 40), (24, 80)),
)
@pytest.mark.parametrize(
    ("view", "visible_marker", "hidden_marker", "omission_marker"),
    (
        (
            build_generic_tool_result_view(
                "remote_tool",
                "\n".join(f"generic result {index}" for index in range(20)),
                ok=True,
            ),
            "generic result 0",
            "generic result 19",
            "… +15 lines",
        ),
        (
            build_native_tool_result_view(
                "shell_command",
                {"command": "printf output"},
                ok=True,
                data={
                    "command": "printf output",
                    "output_lines": [
                        f"shell output {index}" for index in range(20)
                    ],
                },
            ),
            "shell output 19",
            "shell output 2",
            "… +16 lines",
        ),
        (
            build_tool_start_view(
                "js_repl",
                {"code": "\n".join(
                    f"const value{index} = {index};" for index in range(30)
                )},
            ),
            "const value0 =",
            "const value29 = 29;",
            "… +12 lines",
        ),
        (
            build_native_tool_result_view(
                "js_repl",
                {"code": "const value = 1;"},
                ok=True,
                data={
                    "output": "\n".join(
                        f"javascript output {index}" for index in range(30)
                    ),
                },
            ),
                "javascript outp",
            "javascript output 29",
            "… +25 lines",
        ),
        (
            build_batch_start_view(
                (
                    (
                        f"batch_tool_{index}",
                        {
                            **{
                                f"argument_{item}": item
                                for item in range(5)
                            },
                            "tail": f"batch-start-tail-{index}",
                        },
                    )
                    for index in range(6)
                )
            ),
            "batch_tool_0",
            "batch-start-tail-5",
            "… +2 tools",
        ),
        (
            build_batch_completed_view(
                (
                    (
                        f"batch_tool_{index}",
                        True,
                        "\n".join(
                            f"batch result {index}-{line}"
                            for line in range(8)
                        ),
                    )
                    for index in range(6)
                )
            ),
            "batch result",
            "batch result 5-7",
            "… +2 tools",
        ),
    ),
    ids=(
        "generic-result",
        "shell-result",
        "javascript-start",
        "javascript-result",
        "batch-start",
        "batch-result",
    ),
)
async def test_bounded_tool_blocks_keep_real_terminal_layout_stable(
    rows: int,
    columns: int,
    view,
    visible_marker: str,
    hidden_marker: str,
    omission_marker: str,
) -> None:
    with create_pipe_input() as pipe_input:
        runtime = TuiRuntime(input_obj=pipe_input, output_obj=DummyOutput())
        output = TuiOutputControl("", runtime=runtime, animate=False)
        presentation = TuiPresentationSink(output)
        application = TuiApplicationSink(runtime)

        with patch.object(
            runtime.screen.application.output,
            "get_size",
            return_value=Size(rows=rows, columns=columns),
        ):
            await runtime.open()
            try:
                with patch.object(
                    runtime.screen.application,
                    "print_text",
                    wraps=runtime.screen.application.print_text,
                ) as print_text:
                    application.emit(ApplicationView(type="intro"))
                    runtime.append_submitted_query(
                        "exercise bounded tool layout",
                        "turn-tool-layout",
                    )
                    runtime.append_block(
                        _block("\n".join(
                            f"assistant line {index}" for index in range(24)
                        )),
                        kind="assistant",
                    )
                    runtime.set_execution_active(True)
                    runtime.screen.set_activity_renderable(_block("Thinking"))
                    await _render_next_frame(runtime)
                    await _wait_for_scrollback_advance(runtime)

                    await presentation.emit(view)
                    await _render_next_frame(runtime)
                    await _wait_for_scrollback_settlement(runtime)
                    screen = await _render_next_frame(runtime)

                printed = "".join(
                    text
                    for call_args in print_text.call_args_list
                    for _style, text in call_args.args[0]
                )
                visible = f"{printed}\n{_rendered_screen_text(screen)}"
                transcript = _transcript_text(runtime.document)
                positions = screen.visible_windows_to_write_positions
                bottom_windows = (
                    runtime.screen.input_top_padding,
                    runtime.screen.input.window,
                    runtime.screen.input_bottom_padding,
                    runtime.screen.footer_window,
                )
                bottom_positions = [
                    positions[window]
                    for window in bottom_windows
                    if window in positions
                ]
                status_position = positions[runtime.screen.status_window]

                assert f">_ {const.APP_DESC}" in printed
                assert "exercise bounded" in printed
                assert "tool layout" in printed
                assert "assistant line 0" in printed
                assert "assistant line 23" in printed
                assert visible_marker in visible
                assert hidden_marker not in visible
                assert hidden_marker in transcript
                assert omission_marker in visible
                assert runtime.document.scrollback_line_count > 0
                assert runtime.screen.canvas_spacer not in positions
                assert (
                    status_position.ypos + status_position.height
                    <= bottom_positions[0].ypos
                )
                assert all(
                    current.ypos + current.height == following.ypos
                    for current, following in zip(
                        bottom_positions,
                        bottom_positions[1:],
                    )
                )
                assert (
                    bottom_positions[-1].ypos
                    + bottom_positions[-1].height
                    <= rows
                )
                assert all(
                    0 <= row < rows
                    and all(0 <= column < columns for column in cells)
                    for row, cells in screen.data_buffer.items()
                )
            finally:
                runtime.screen.clear_activity_renderable()
                runtime.set_execution_active(False)
                await output.stop()
                await runtime.close()


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
    assert "result line 79" not in display
    assert "result line 0" in transcript
    assert "result line 79" in transcript
    assert "(F12 to view transcript)" not in transcript


@pytest.mark.parametrize(
    ("width", "expected_hint"),
    (
        (20, "… +4 lines"),
        (39, "… +4 lines Ctrl+T"),
        (40, "… +4 lines Ctrl+T"),
    ),
)
@pytest.mark.anyio
async def test_shell_transcript_hint_uses_one_visual_row(
    width: int,
    expected_hint: str,
) -> None:
    runtime = TuiRuntime()
    runtime.screen._output_size = lambda: (width, 24)
    output = TuiOutputControl("", runtime=runtime, animate=False)
    presentation = TuiPresentationSink(output)
    output_lines = [f"output line {index}" for index in range(8)]

    await presentation.emit(build_native_tool_result_view(
        "shell_command",
        {"command": "printf output"},
        ok=True,
        data={
            "command": "printf output",
            "output_lines": output_lines,
        },
        call_id="transcript-hint-width",
    ))

    display = fragments_text(runtime.document.fragments(width=width))
    display_lines = display.splitlines()
    transcript = _transcript_text(runtime.document)

    assert f"    {expected_hint}" in display_lines
    assert display_lines[-1] == "    output line 7"
    assert all(get_cwidth(line) <= width for line in display_lines)
    assert display_line_count(display, width=width) == len(display_lines)
    assert output_lines[-1] in transcript
    assert expected_hint not in transcript


@pytest.mark.anyio
async def test_stable_shell_display_reflows_only_after_resize_settles() -> None:
    runtime = TuiRuntime()
    runtime.screen._output_size = lambda: (20, 24)
    output = TuiOutputControl("", runtime=runtime, animate=False)
    presentation = TuiPresentationSink(output)
    command = "python3 -c \"print('" + "界" * 80 + "')\""
    output_lines = [
        f"output {index} " + "👩\u200d💻" * 30
        for index in range(8)
    ]

    await presentation.emit(build_native_tool_result_view(
        "shell_command",
        {"command": command},
        ok=True,
        data={
            "command": command,
            "output_lines": output_lines,
        },
        call_id="resize-stable-shell",
    ))

    def display_at(width: int, *, reflow_sources: bool) -> str:
        return fragments_text(runtime.document.fragments(
            width=width,
            reflow_sources=reflow_sources,
        ))

    narrow = display_at(20, reflow_sources=True)
    transcript = _transcript_text(runtime.document)
    raw_texts = tuple(item.raw_text for item in runtime.document.blocks)
    gaps = tuple(item.gap_before for item in runtime.document.blocks)

    runtime.document.set_display_width(80, reflow_sources=False)
    assert display_at(80, reflow_sources=False) == narrow

    runtime.document.set_display_width(80, reflow_sources=True)
    wide = display_at(80, reflow_sources=False)

    assert wide != narrow
    assert "… +4 lines" in narrow
    assert "… +4 lines (Ctrl+T to view transcript)" in wide
    assert get_cwidth(narrow.splitlines()[0]) <= 20
    assert get_cwidth(wide.splitlines()[0]) <= 80
    assert get_cwidth(wide.splitlines()[0]) > get_cwidth(
        narrow.splitlines()[0]
    )
    assert all(get_cwidth(line) <= 80 for line in wide.splitlines())
    assert _transcript_text(runtime.document) == transcript
    assert output_lines[-1] in transcript
    assert tuple(item.raw_text for item in runtime.document.blocks) == raw_texts
    assert tuple(item.gap_before for item in runtime.document.blocks) == gaps

    runtime.document.set_display_width(20, reflow_sources=False)
    assert display_at(20, reflow_sources=False) == wide

    runtime.document.set_display_width(20, reflow_sources=True)
    assert display_at(20, reflow_sources=False) == narrow
    assert _transcript_text(runtime.document) == transcript


@pytest.mark.anyio
@pytest.mark.parametrize("view", (
    build_batch_completed_view(((
        "remote_tool_with_a_long_name",
        True,
        "batch result " + ("界🙂" * 40),
    ),)),
    PlanUpdateView(
        explanation="explanation " * 12,
        items=(PlanItemView(
            step="implement a long plan step " * 8,
            status="in_progress",
        ),),
    ),
), ids=("batch", "plan"))
async def test_tree_views_reflow_from_structured_source_after_resize(view) -> None:
    runtime = TuiRuntime()
    runtime.screen._output_size = lambda: (20, 24)
    output = TuiOutputControl("", runtime=runtime, animate=False)
    presentation = TuiPresentationSink(output)

    await presentation.emit(view)

    narrow = fragments_text(runtime.document.fragments(
        width=20,
        reflow_sources=True,
    ))
    transcript = _transcript_text(runtime.document)
    assert runtime.document.blocks[0].display_renderer is not None
    assert all(get_cwidth(line) <= 20 for line in narrow.splitlines())

    runtime.document.set_display_width(60, reflow_sources=True)
    wide = fragments_text(runtime.document.fragments(
        width=60,
        reflow_sources=False,
    ))

    assert wide != narrow
    assert all(get_cwidth(line) <= 60 for line in wide.splitlines())
    assert _transcript_text(runtime.document) == transcript


@pytest.mark.anyio
async def test_shell_resize_replays_new_width_through_native_scrollback() -> None:
    with create_pipe_input() as pipe_input:
        runtime = TuiRuntime(input_obj=pipe_input, output_obj=DummyOutput())
        terminal_size = Size(rows=8, columns=20)

        with patch.object(
            runtime.screen.application.output,
            "get_size",
            side_effect=lambda: terminal_size,
        ):
            await runtime.open()
            try:
                output = TuiOutputControl("", runtime=runtime, animate=False)
                presentation = TuiPresentationSink(output)
                command = "python3 -c \"print('" + "界" * 80 + "')\""
                output_lines = [
                    f"output {index} " + "👩\u200d💻" * 30
                    for index in range(8)
                ]

                await presentation.emit(build_native_tool_result_view(
                    "shell_command",
                    {"command": command},
                    ok=True,
                    data={
                        "command": command,
                        "output_lines": output_lines,
                    },
                    call_id="resize-scrollback-shell",
                ))
                await asyncio.sleep(0.02)

                assert runtime.document.scrollback_line_count > 0
                transcript = _transcript_text(runtime.document)

                with patch.object(
                    runtime.screen,
                    "clear_terminal_for_resize_replay",
                ) as clear, patch.object(
                    runtime.screen.application,
                    "print_text",
                    wraps=runtime.screen.application.print_text,
                ) as print_text:
                    terminal_size = Size(rows=8, columns=80)
                    runtime.viewport.observe_terminal_geometry(80, 8)
                    await asyncio.sleep(0.12)

                replayed = "".join(
                    text
                    for call in print_text.call_args_list
                    for _style, text in call.args[0]
                )

                clear.assert_called_once_with()
                assert print_text.called
                assert replayed.count("• Ran ") == 1
                assert "… +4 lines (Ctrl+T to view transcript)" in replayed
                assert all(
                    get_cwidth(line) <= 80
                    for line in replayed.splitlines()
                )
                assert _transcript_text(runtime.document) == transcript
                assert command in transcript
                assert output_lines[-1] in transcript
                assert runtime.viewport._reflowed_geometry == (80, 8)
            finally:
                await runtime.close()


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
    assert output_lines[-1] in display
    assert output_lines[2] not in display
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
    assert "\n  └ ready" in document_text
    assert transcript_text.count("• JavaScript") == 2
    assert "host.tool('shell_command'" in transcript_text
    assert "\nready" in transcript_text
    assert "\n└ ready" not in transcript_text
    assert "\n\n• JavaScript\n  └ ready" in document_text
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
    assert lines[javascript_line - 2:javascript_line] == [" ", ""]
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
    assert "JavaScript cell completed.\n\n─" in final_text
    assert "─\n\n• 已执行完成。" in final_text


@pytest.mark.anyio
@pytest.mark.parametrize("terminal_rows", (8, 12, 24))
async def test_oversized_javascript_preview_pushes_title_to_native_scrollback(
    terminal_rows: int,
) -> None:
    with create_pipe_input() as pipe_input:
        runtime = TuiRuntime(input_obj=pipe_input, output_obj=DummyOutput())
        output = TuiOutputControl("", runtime=runtime, animate=False)
        presentation = TuiPresentationSink(output)
        application = TuiApplicationSink(runtime)
        arguments = {
            "code": "\n".join(
                f"const value{index} = {index};" for index in range(30)
            ),
            "timeout_ms": 30000,
        }

        with patch.object(
            runtime.screen.application.output,
            "get_size",
            return_value=Size(rows=terminal_rows, columns=40),
        ):
            await runtime.open()
            try:
                with patch.object(
                    runtime.screen.application,
                    "print_text",
                    wraps=runtime.screen.application.print_text,
                ) as print_text:
                    application.emit(ApplicationView(type="intro"))
                    runtime.append_submitted_query(
                        "run javascript",
                        "turn-javascript",
                    )
                    await _render_next_frame(runtime)
                    before_tool = runtime.document.scrollback_line_count

                    runtime.set_execution_active(True)
                    await presentation.emit(build_tool_start_view(
                        "js_repl",
                        arguments,
                        call_id="call-javascript",
                    ))
                    await _render_next_frame(runtime)
                    await _wait_for_scrollback_advance(
                        runtime,
                        after=before_tool,
                    )
                    await presentation.emit(build_native_tool_result_view(
                        "js_repl",
                        arguments,
                        ok=True,
                        data={
                            "output": "\n".join(
                                f"result {index}" for index in range(30)
                            ),
                        },
                        call_id="call-javascript",
                    ))
                    completed_screen = await _render_next_frame(runtime)
                    await asyncio.sleep(0.02)

                printed = "".join(
                    text
                    for call_args in print_text.call_args_list
                    for _style, text in call_args.args[0]
                )
                visible = f"{printed}\n{_rendered_screen_text(completed_screen)}"
                transcript = _transcript_text(runtime.document)

                assert f">_ {const.APP_DESC}" in printed
                assert "run javascript" in printed
                assert visible.count("• JavaScript") >= 2
                assert "const value0 = 0;" in visible
                assert "const value29 = 29;" not in visible
                assert "result 0" in visible
                assert "result 29" not in visible
                assert "… +12 lines" in visible
                assert "… +25 lines" in visible
                assert "const value29 = 29;" in transcript
                assert "result 29" in transcript
            finally:
                runtime.set_execution_active(False)
                await output.stop()
                await runtime.close()


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

    final_lines = _document_text(runtime.document).splitlines()
    assert final_lines[0].startswith("• ")
    assert all(line.startswith("  ") for line in final_lines[1:])
    assert "".join(line[2:] for line in final_lines) == source


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


def test_transcript_display_metrics_cache_tracks_layout_dependencies() -> None:
    runtime = TuiRuntime()
    size = Size(rows=24, columns=20)
    runtime.set_active_renderable(
        FragmentBlock(((ASSISTANT_PREFIX_CLASS, f"• {'x' * 37}"),)),
        kind="assistant",
    )

    with (
        patch.object(
            runtime.screen.application.output,
            "get_size",
            side_effect=lambda: size,
        ),
        patch(
            "frontends.tui.core.screen.display_line_count",
            wraps=display_line_count,
        ) as line_count,
        patch.object(
            runtime.screen,
            "_transcript_continuation_widths",
            wraps=runtime.screen._transcript_continuation_widths,
        ) as continuation_widths,
    ):
        def transcript_scan_count() -> int:
            return sum(
                "continuation_widths" in item.kwargs
                for item in line_count.call_args_list
            )

        runtime.screen._transcript_cursor()
        assert transcript_scan_count() == 0
        assert continuation_widths.call_count == 0

        assert runtime.screen._transcript_dimension().preferred == 3
        assert runtime.screen._transcript_dimension().preferred == 3

        assert transcript_scan_count() == 1
        assert continuation_widths.call_count == 1

        with patch.object(
            runtime.screen,
            "transcript_available_height",
            return_value=2,
        ):
            assert runtime.screen._transcript_dimension().preferred == 2

        assert transcript_scan_count() == 1

        runtime.set_active_renderable(
            FragmentBlock(((ASSISTANT_PREFIX_CLASS, f"• {'x' * 57}"),)),
            kind="assistant",
        )
        assert runtime.screen._transcript_dimension().preferred == 4
        assert transcript_scan_count() == 2

        size = Size(rows=24, columns=30)
        assert runtime.screen._transcript_dimension().preferred == 3
        assert transcript_scan_count() == 3


def test_transcript_display_metrics_cache_tracks_visible_prefix() -> None:
    runtime = TuiRuntime()
    runtime.append_block(_block("first\nsecond\nthird"), kind="assistant")

    with patch(
        "frontends.tui.core.screen.display_line_count",
        wraps=display_line_count,
    ) as line_count:
        def transcript_scan_count() -> int:
            return sum(
                "continuation_widths" in item.kwargs
                for item in line_count.call_args_list
            )

        assert runtime.screen._transcript_dimension().preferred == 3
        assert runtime.screen._transcript_dimension().preferred == 3
        assert transcript_scan_count() == 1

        assert runtime.document.commit_scrollback_prefix(
            1,
            expected_start=0,
        )
        assert runtime.screen._transcript_dimension().preferred == 2
        assert transcript_scan_count() == 2


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
async def test_attempt_supersede_appends_notice_and_new_assistant_block() -> None:
    runtime = TuiRuntime()
    output = TuiOutputControl("", runtime=runtime, animate=False)

    await output.append_assistant_delta("old partial")
    await output.settle_stream()
    with (
        patch.object(runtime.screen, "clear_terminal_scrollback") as clear_scrollback,
        patch.object(runtime.screen.application.renderer, "clear") as clear_renderer,
    ):
        await output.supersede_assistant_presentation()
        await output.append_assistant_delta("new answer")
        await output.settle_stream()

    assert _document_text(runtime.document) == "\n".join((
        "• old partial",
        "",
        "↻ Previous attempt interrupted; retrying",
        "",
        "• new answer",
    ))
    assert not runtime.document.blocks[-1].stream_continuation
    assert runtime.document.active_gap_before == 1
    assert runtime.document.visible_prefix_line_count == 0
    clear_scrollback.assert_not_called()
    clear_renderer.assert_not_called()


@pytest.mark.anyio
@pytest.mark.parametrize("stable_line_count", (0, 40))
@pytest.mark.parametrize("surface", ("input", "slash", "skill", "skills_menu"))
async def test_attempt_supersede_preserves_live_tui_surfaces_without_blank_frames(
    stable_line_count: int,
    surface: str,
) -> None:
    with create_pipe_input() as pipe_input:
        runtime = TuiRuntime(input_obj=pipe_input, output_obj=DummyOutput())
        output = TuiOutputControl("", runtime=runtime, animate=False)
        runtime.input_model.set_skills(tuple(
            _spacing_test_skill(f"skill-{index:02d}")
            for index in range(12)
        ))
        menu_task = None

        with patch.object(
            runtime.screen.application.output,
            "get_size",
            return_value=Size(rows=24, columns=40),
        ):
            await runtime.open()
            try:
                if stable_line_count:
                    runtime.append_block(
                        _block("\n".join(
                            f"stable {index:02d}"
                            for index in range(stable_line_count)
                        )),
                        kind="assistant",
                    )
                    await _render_next_frame(runtime)

                runtime.set_execution_active(True)
                await output.append_assistant_delta("old partial")
                await output.settle_stream()
                await _render_next_frame(runtime)

                if surface == "input":
                    pipe_input.send_text("draft")
                    await _wait_for_input_text(runtime, "draft")
                elif surface in {"slash", "skill"}:
                    pipe_input.send_text("/" if surface == "slash" else "$")
                    for _ in range(100):
                        await asyncio.sleep(0.002)
                        state = runtime.screen.input.buffer.complete_state
                        if state is not None and state.completions:
                            break
                    else:
                        raise AssertionError("completion did not become ready")
                    runtime.input_model._select_completion(
                        runtime.screen.input.buffer,
                        1,
                    )
                else:
                    menu_task = asyncio.create_task(runtime.select_menu(
                        MenuRequest(
                            title="Skills",
                            options=tuple(
                                MenuOption(index, f"Skill {index}")
                                for index in range(12)
                            ),
                        ),
                    ))
                    for _ in range(20):
                        await asyncio.sleep(0)
                        if runtime.screen.menu.active:
                            break
                    assert runtime.screen.menu.active
                    runtime.screen.menu._move(1)

                before_screen = await _render_next_frame(runtime)
                before_positions = before_screen.visible_windows_to_write_positions
                anchor_window = (
                    runtime.screen.menu_window
                    if surface == "skills_menu"
                    else runtime.screen.input.window
                )
                before_anchor = before_positions[anchor_window]
                before_row = 24 - before_screen.height + before_anchor.ypos
                before_focus = runtime.screen.application.layout.current_window
                before_input = runtime.screen.input.buffer.text
                before_completion = (
                    runtime.screen.input.buffer.complete_state.complete_index
                    if runtime.screen.input.buffer.complete_state is not None
                    else None
                )
                before_menu_selection = (
                    runtime.screen.menu.state.selected
                    if runtime.screen.menu.state is not None
                    else None
                )

                frames = []

                def capture_frame(_application) -> None:
                    screen = runtime.screen.application.renderer.last_rendered_screen
                    positions = screen.visible_windows_to_write_positions
                    transcript = positions.get(runtime.screen.transcript_window)
                    input_position = positions.get(runtime.screen.input.window)
                    transcript_text = ""
                    input_text = ""
                    if transcript is not None:
                        transcript_text = "".join(
                            cells[column].char
                            for row, cells in screen.data_buffer.items()
                            if transcript.ypos <= row < transcript.ypos + transcript.height
                            for column in sorted(cells)
                        ).strip()
                    if input_position is not None:
                        input_text = "".join(
                            cells[column].char
                            for row, cells in screen.data_buffer.items()
                            if input_position.ypos <= row < input_position.ypos + input_position.height
                            for column in sorted(cells)
                        ).strip()
                    frames.append((
                        bool(transcript_text),
                        bool(input_text),
                        runtime.screen.canvas_spacer in positions,
                        _first_nonblank_screen_row(screen),
                    ))

                runtime.screen.application.after_render += capture_frame
                await output.supersede_assistant_presentation()
                await output.append_assistant_delta("new answer")
                await output.settle_stream()
                after_screen = await _render_next_frame(runtime)
                await asyncio.sleep(0)
                runtime.screen.application.after_render -= capture_frame

                after_positions = after_screen.visible_windows_to_write_positions
                after_anchor = after_positions[anchor_window]
                after_row = 24 - after_screen.height + after_anchor.ypos

                assert frames
                assert len(frames) <= 2
                assert all(body or prompt for body, prompt, _spacer, _row in frames)
                assert all(not spacer for _body, _prompt, spacer, _row in frames)
                assert runtime.screen.application.layout.current_window is before_focus
                assert runtime.screen.input.buffer.text == before_input
                assert (
                    runtime.screen.input.buffer.complete_state.complete_index
                    if runtime.screen.input.buffer.complete_state is not None
                    else None
                ) == before_completion
                assert (
                    runtime.screen.menu.state.selected
                    if runtime.screen.menu.state is not None
                    else None
                ) == before_menu_selection
                assert runtime.screen.canvas_spacer not in after_positions
                assert _first_nonblank_screen_row(after_screen) >= 0
                assert after_row >= 0
                assert before_row >= 0

                assert _transcript_text(runtime.document).endswith("\n".join((
                    "• old partial",
                    "",
                    "↻ Previous attempt interrupted; retrying",
                    "",
                    "• new answer",
                )))
            finally:
                if runtime.screen.menu.active:
                    runtime.screen.menu.finish(None)
                if menu_task is not None:
                    await menu_task
                runtime.set_execution_active(False)
                await runtime.close()


@pytest.mark.anyio
async def test_attempt_supersede_separates_retry_notice_from_failure() -> None:
    runtime = TuiRuntime()
    output = TuiOutputControl("", runtime=runtime, animate=False)
    presentation = TuiPresentationSink(output)

    await presentation.emit(build_failure_view(
        "turn.failed",
        "responses stream ended incomplete: max_output_tokens",
    ))
    await output.supersede_assistant_presentation()

    assert _document_text(runtime.document) == "\n".join((
        "■ turn.failed",
        "  └ responses stream ended incomplete: max_output_tokens",
        "",
        "↻ Previous attempt interrupted; retrying",
    ))
    assert [item.stream_continuation for item in runtime.document.blocks] == [
        False,
        False,
    ]


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
            "frontends.tui.adapters.markdown.render_tui_markdown",
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


def test_active_stream_metadata_update_preserves_visual_revision() -> None:
    runtime = TuiRuntime()
    block = _block("tail")

    assert runtime.set_active_renderable(
        block,
        kind="assistant",
        raw_text="before",
    )
    revision = runtime.document.active_transcript_revision

    with patch.object(
        runtime.screen,
        "invalidate",
        wraps=runtime.screen.invalidate,
    ) as invalidate:
        assert not runtime.set_active_renderable(
            block,
            kind="assistant",
            raw_text="after",
        )

    assert runtime.document.active_transcript_revision == revision
    assert runtime.document.active_raw_text == "after"
    invalidate.assert_not_called()


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
        "frontends.tui.adapters.markdown.render_tui_markdown",
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
                outer_inset = positions[
                    runtime.screen.bottom_pane_top_inset.content
                ]
                top_padding = positions[runtime.screen.input_top_padding]

                assert (
                    24
                    - runtime.screen._visible_height()
                    + position.ypos
                    == input_row
                )
                assert runtime.screen.canvas_spacer not in positions
                assert outer_inset.ypos == (
                    transcript.ypos + transcript.height
                )
                assert top_padding.ypos == outer_inset.ypos + 1
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


@pytest.mark.anyio
async def test_final_stream_handoff_merges_render_requests() -> None:
    runtime = TuiRuntime()
    output = TuiOutputControl("", runtime=runtime, animate=False)
    output.assistant.append("final line")

    with patch.object(runtime.screen, "_invalidate_now") as invalidate:
        await output._commit_current()

    invalidate.assert_called_once_with()
    assert runtime.document.active_block is None
    assert _document_text(runtime.document) == "• final line"


@pytest.mark.anyio
async def test_large_final_stream_render_yields_the_event_loop(
    monkeypatch,
) -> None:
    runtime = TuiRuntime()
    output = TuiOutputControl("", runtime=runtime, animate=False)
    started = threading.Event()
    release = threading.Event()
    render_thread: list[int] = []

    def render(
        _text: str,
        _width: int,
        *,
        hyperlinks: bool,
        continuation: bool,
    ) -> FragmentBlock:
        _ = hyperlinks, continuation
        render_thread.append(threading.get_ident())
        started.set()
        if not release.wait(timeout=1.0):
            raise AssertionError("event loop did not release final render")
        return FragmentBlock((("", "• rendered"),))

    monkeypatch.setattr(
        tui_output_module,
        "render_tui_assistant_markdown",
        render,
    )
    output.assistant.append(
        "x" * tui_output_module.FINAL_RENDER_ASYNC_MIN_SIZE
    )

    commit = asyncio.create_task(output._commit_current())
    async with asyncio.timeout(1.0):
        while not started.is_set():
            await asyncio.sleep(0.001)

    assert started.is_set()
    assert not commit.done()
    assert len(render_thread) == 1
    assert render_thread[0] != threading.get_ident()

    release.set()
    assert await commit
    assert _document_text(runtime.document) == "• rendered"


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
                await output._commit_current()

                for _ in range(20):
                    await asyncio.sleep(0)
                    if frames:
                        break

                assert frames
                assert all(frame == streamed for frame in frames)
            finally:
                await runtime.close()


@pytest.mark.anyio
async def test_execution_end_coalesces_an_unchanged_final_canvas_frame() -> None:
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
                runtime.set_execution_active(True)
                await output.append_assistant_delta("first\nlast")
                await output.settle_stream()
                await _render_next_frame(runtime)

                await output._commit_current()
                await _render_next_frame(runtime)
                render_revision = runtime.screen.application.render_counter

                runtime.set_execution_active(False)
                for _ in range(20):
                    await asyncio.sleep(0)
                    if (
                        runtime.screen.application.render_counter
                        > render_revision
                    ):
                        break

                assert runtime.screen.application.render_counter == (
                    render_revision + 2
                )
                assert not runtime.execution_active
            finally:
                runtime.set_execution_active(False)
                await output.stop()
                await runtime.close()


@pytest.mark.anyio
async def test_execution_end_renders_changed_queue_hint() -> None:
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
                runtime.set_execution_active(True)
                runtime.screen.input.buffer.text = "draft"
                runtime.screen.input.buffer.cursor_position = 5
                await output.append_assistant_delta("first\nlast")
                await output.settle_stream()
                await _render_next_frame(runtime)

                await output._commit_current()
                await _render_next_frame(runtime)
                render_revision = runtime.screen.application.render_counter
                assert "tab to queue" in fragments_text(
                    runtime.screen._footer_fragments()
                )

                runtime.set_execution_active(False)
                for _ in range(20):
                    await asyncio.sleep(0)
                    if (
                        runtime.screen.application.render_counter
                        > render_revision
                    ):
                        break

                assert runtime.screen.application.render_counter == (
                    render_revision + 2
                )
                assert "tab to queue" not in fragments_text(
                    runtime.screen._footer_fragments()
                )
            finally:
                runtime.set_execution_active(False)
                await output.stop()
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
        "frontends.tui.adapters.markdown.render_tui_markdown",
        wraps=render_tui_markdown,
    ) as render:
        await content.emit(AssistantTextDelta("**bold**", RESPONSE_IDENTITY))
        await content.emit(AssistantSegmentCompleted(RESPONSE_IDENTITY))

        render.assert_not_called()
        assert _document_text(runtime.document) == "• bold"
        first_fragments = runtime.document.active_block.fragments
        assert any("bold" in style for style, text in first_fragments if text)

        await content.emit(AssistantTextDelta(" and `code`", RESPONSE_IDENTITY))
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


@pytest.mark.parametrize(
    "source",
    ("```\nvalue\n```\n", "```unknown-language\nvalue\n```\n"),
)
def test_streaming_code_fallback_matches_codex_default_style(source: str) -> None:
    renderer = TuiMarkdownStreamRenderer()

    streamed = renderer.render(source, width=40)
    committed = render_tui_markdown(source, width=40)

    assert streamed.fragments == (("", "value"),)
    assert committed.fragments == (("", "value"),)


@pytest.mark.anyio
async def test_assistant_commit_falls_back_to_plain_text_after_markdown_failure() -> None:
    runtime = TuiRuntime()
    output = TuiOutputControl("", runtime=runtime, animate=False)
    presentation = TuiPresentationSink(output)

    await output.append_assistant_delta("```\nvalue\n```")
    with patch(
        "frontends.tui.adapters.markdown.render_tui_markdown",
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

    await content.emit(AssistantTextDelta("answer", RESPONSE_IDENTITY))
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
