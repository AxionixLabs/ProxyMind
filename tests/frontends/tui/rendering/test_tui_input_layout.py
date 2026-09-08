# -*- coding: utf-8 -*-

"""验证输入、菜单、审批和底部区域的动态布局。

这些区域参与同一高度分配算法，维持整体可直接审计相互挤压的不变量。
"""


import asyncio
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
from agent.application.approvals.coordinator import ApprovalCoordinator
from agent.application.approvals.models import ApprovalDecisionValue
from frontends.interaction.contracts import PromptContext
from agent.application.views.builders.tools import (
    build_generic_tool_result_view,
    build_native_tool_result_view,
    build_tool_start_view,
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
    first_nonblank_screen_row as _first_nonblank_screen_row,
    render_next_frame as _render_next_frame,
    rendered_screen_text as _rendered_screen_text,
    transcript_text as _transcript_text,
    wait_for_input_text as _wait_for_input_text,
    wait_for_scrollback_advance as _wait_for_scrollback_advance,
    wait_for_scrollback_settlement as _wait_for_scrollback_settlement,
)


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

            pipe_input.send_text("\x15" * (2 * (pasted.count("\n") + 1)))
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

            pipe_input.send_text("\n" * expansion_rows)
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
                        pipe_input.send_text("\n")
                        await _wait_for_input_text(
                            runtime,
                            "x" + "\n" * line_count,
                        )
                        await _render_next_frame(runtime)
                else:
                    pipe_input.send_text("x" + "\n" * 8)
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

                pipe_input.send_text("\x15" * (2 * (value.count("\n") + 1)))
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

            pipe_input.send_text("\n" * expansion_rows)
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

            pipe_input.send_text("\n" * 20)
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
                pipe_input.send_text("\n")
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

            pipe_input.send_text("\n" * 20)
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
    ("ctrl_u", "ctrl_w", "delete", "history"),
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
                else:
                    pipe_input.send_text(f"\x1b[200~{pasted}\x1b[201~")
                await _wait_for_input_text(runtime, pasted)
                await _render_next_frame(runtime)
                assert runtime.screen._input_height() > 1

                runtime.screen.clear_activity_renderable()
                if clear_mode == "ctrl_u":
                    pipe_input.send_text(
                        "\x15" * (2 * (pasted.count("\n") + 1))
                    )
                elif clear_mode == "ctrl_w":
                    pipe_input.send_text("\x17" * 16)
                elif clear_mode == "delete":
                    buffer.cursor_position = 0
                    pipe_input.send_text("\x1b[3~" * len(pasted))
                elif clear_mode == "history":
                    pipe_input.send_text("\x1b[B")
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

                pipe_input.send_text("\n" * 8)
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
                if runtime.screen.approval.state is not None:
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
            assert runtime.screen.status_window not in positions
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
