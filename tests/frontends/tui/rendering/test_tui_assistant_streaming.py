# -*- coding: utf-8 -*-

"""验证 assistant 流式文本的提交边界、动画与最终交接。"""


import asyncio
import threading
import typing
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
from agent.ports import (
    AssistantBuffered,
    AssistantOutputBoundary,
    AssistantSegmentCompleted,
    AssistantTextDelta,
    ModelWaitRequested,
    OutputSurfaceContext,
    ResponseIdentity,
    SourcesOutput,
)
from agent.application.views.builders.lifecycle import build_failure_view
from agent.application.views.builders.tools import (
    build_generic_tool_result_view,
    build_native_tool_result_view,
    build_tool_start_view,
)
from frontends.terminal.capabilities import TerminalCapabilities
from frontends.tui.adapters.content import TuiContentSink
from frontends.tui.adapters import output as tui_output_module
from frontends.tui.adapters.markdown import (
    TuiMarkdownStreamRenderer,
    render_tui_assistant_markdown,
    render_tui_markdown,
)
from frontends.tui.adapters.output import TuiOutputControl
from frontends.tui.adapters.presentation import TuiPresentationSink
from frontends.tui.core.models import (
    FragmentBlock,
    LineFill,
    MenuOption,
    MenuRequest,
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
    block as _block,
    document_text as _document_text,
    first_nonblank_screen_row as _first_nonblank_screen_row,
    render_next_frame as _render_next_frame,
    spacing_test_skill as _spacing_test_skill,
    transcript_text as _transcript_text,
    wait_for_input_text as _wait_for_input_text,
)


RESPONSE_IDENTITY = ResponseIdentity("turn_test", 1, 1, 1)


OUTPUT_SURFACE_CONTEXT = OutputSurfaceContext(
    surface_id="surface_test",
    cid="cid_test",
    sid="sid_test",
    turn_id="turn_test",
    agent_id="root",
)


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
            terminal_capabilities=runtime.terminal_capabilities,
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
            terminal_capabilities=runtime.terminal_capabilities,
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
        terminal_capabilities: TerminalCapabilities,
    ) -> FragmentBlock:
        _ = hyperlinks, continuation
        assert terminal_capabilities is runtime.terminal_capabilities
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
    assert any(
        style == "class:terminal.accent" and text == "code"
        for style, text in fragments
    )


@pytest.mark.anyio
async def test_segment_completion_keeps_rendered_markdown_stable() -> None:
    runtime = TuiRuntime()
    output = TuiOutputControl("", runtime=runtime, animate=False)
    content = TuiContentSink(
        output,
        surface_context=OUTPUT_SURFACE_CONTEXT,
    )

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
            terminal_capabilities=runtime.terminal_capabilities,
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
    content = TuiContentSink(
        output,
        surface_context=OUTPUT_SURFACE_CONTEXT,
    )

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
