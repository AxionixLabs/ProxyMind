# -*- coding: utf-8 -*-

"""验证 TUI 文档块、稳定前缀与原生滚屏提交。

这些断言共享完整文档提交序列，维持整体可保证前缀与滚屏边界同步演进。
"""


import asyncio
import io
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
from agent.application.approvals.coordinator import ApprovalCoordinator
from frontends.interaction.contracts import PromptContext
from agent.application.views.builders.approval import build_approval_view
from frontends.terminal.text import sanitize_terminal_text
from agent.application.views.builders.tools import (
    build_generic_tool_result_view,
    build_native_tool_result_view,
    build_tool_start_view,
)
from frontends.tui.adapters import markdown as tui_markdown
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
from tests.frontends.tui.rendering.frame_scenarios import (
    AlternateScreenOutput as _AlternateScreenOutput,
    KnownInlineHeightOutput as _KnownInlineHeightOutput,
    block as _block,
    document_text as _document_text,
    render_next_frame as _render_next_frame,
    rendered_screen_text as _rendered_screen_text,
    spacing_test_skill as _spacing_test_skill,
    transcript_text as _transcript_text,
    wait_for_input_text as _wait_for_input_text,
    wait_for_scrollback_advance as _wait_for_scrollback_advance,
    wait_for_scrollback_settlement as _wait_for_scrollback_settlement,
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

    fragments = queued.fragments(width=24, edit_binding="alt + ↑")
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
                    await _wait_for_scrollback_advance(runtime)

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
