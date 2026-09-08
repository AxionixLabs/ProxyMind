# -*- coding: utf-8 -*-

"""验证补全弹层与活动内容、输入区域之间的布局生命周期。"""


import asyncio
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import pytest
from prompt_toolkit.completion import Completion
from prompt_toolkit.data_structures import Size
from prompt_toolkit.document import Document
from prompt_toolkit.input.defaults import create_pipe_input
from prompt_toolkit.output import DummyOutput
from prompt_toolkit.utils import get_cwidth
from agent.application.approvals.coordinator import ApprovalCoordinator
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
from infrastructure.skills import SkillSpec
from frontends.interaction.contracts import PromptContext
from frontends.tui.adapters.output import TuiOutputControl
from frontends.tui.core.models import (
    FragmentBlock,
    MenuOption,
    MenuRequest,
)
from frontends.tui.core.render import fragments_text
from frontends.tui.core.runtime import TuiRuntime
from frontends.tui.core.styles import text_block
from frontends.tui.core.token_menu import (
    TokenCompletionMenuControl,
    TokenMenuItem,
    TokenMenuSnapshot,
    token_menu_display_height,
)
from frontends.tui.prompting.skills import (
    skill_display_text,
    skill_meta_text,
    skill_completions,
    skill_query_token,
)
from frontends.tui.session.barriers import TuiForegroundTasks


async def wait_for_completion(runtime: TuiRuntime) -> None:
    """等待当前输入对应的异步补全结果就绪。"""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + 1.0
    while loop.time() < deadline:
        state = runtime.screen.input.buffer.complete_state
        if state is not None and state.completions:
            return
        await asyncio.sleep(0.001)
    raise AssertionError("completion did not become ready")


async def wait_for_input_text(runtime: TuiRuntime, text: str) -> None:
    """等待管道输入被主输入框完整消费。"""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + 1.0
    while loop.time() < deadline:
        if runtime.screen.input.buffer.text == text:
            return
        await asyncio.sleep(0.001)
    raise AssertionError(f"input text did not become {text!r}")


async def render_next_frame(runtime: TuiRuntime):
    """触发渲染并返回完成后的屏幕。"""
    previous_revision = runtime.screen.application.render_counter
    runtime.invalidate()
    for _ in range(20):
        await asyncio.sleep(0)
        if runtime.screen.application.render_counter > previous_revision:
            return runtime.screen.application.renderer.last_rendered_screen
    raise AssertionError("next frame was not rendered")


def rendered_input_line(runtime: TuiRuntime) -> str:
    """返回最近一次渲染中的首行输入文本。"""
    return rendered_input_lines(runtime)[0]


def rendered_input_lines(runtime: TuiRuntime) -> list[str]:
    """返回最近一次渲染中的完整输入区域文本。"""
    screen = runtime.screen.application.renderer.last_rendered_screen
    prompt_position = screen.visible_windows_to_write_positions[
        runtime.screen.input_prompt_window
    ]
    input_position = screen.visible_windows_to_write_positions[
        runtime.screen.input.window
    ]
    end_column = input_position.xpos + input_position.width
    return [
        "".join(
            screen.data_buffer[row][column].char
            for column in range(prompt_position.xpos, end_column)
        ).rstrip()
        for row in range(
            input_position.ypos,
            input_position.ypos + input_position.height,
        )
    ]


def skill_spec(name: str, description: str | None = None) -> SkillSpec:
    """创建输入补全测试使用的 skill 描述。"""
    entry = Path(f"{name}/SKILL.md")
    return SkillSpec(
        name=name,
        description=description or f"Use {name}",
        source="test",
        root=entry.parent,
        entry=entry,
    )


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("terminal_rows", "expected_inset", "expected_popup"),
    (
        (3, 1, 0),
        (4, 1, 0),
        (5, 1, 1),
        (10, 1, 6),
        (12, 1, 8),
    ),
)
async def test_completion_budget_shrinks_popup_before_input_surface(
    terminal_rows: int,
    expected_inset: int,
    expected_popup: int,
) -> None:
    with create_pipe_input() as pipe_input:
        runtime = TuiRuntime(input_obj=pipe_input, output_obj=DummyOutput())
        runtime.input_model.set_skills(tuple(
            skill_spec(f"skill-{index:02d}")
            for index in range(12)
        ))

        with patch.object(
            runtime.screen.application.output,
            "get_size",
            return_value=Size(rows=terminal_rows, columns=40),
        ):
            await runtime.open()
            try:
                pipe_input.send_text("$")
                await wait_for_completion(runtime)

                pane_layout = runtime.screen._bottom_pane_layout()
                layout = pane_layout.composer

                assert pane_layout.outer_top_inset_height == expected_inset
                assert layout.input_surface_height == 3
                assert layout.popup_height == expected_popup
                assert layout.footer_height == 0
                assert (
                    pane_layout.total_height <= terminal_rows
                    if terminal_rows >= 4
                    else pane_layout.total_height == 4
                )
            finally:
                await runtime.close()


@pytest.mark.anyio
async def test_slash_completion_has_no_inline_ghost_text() -> None:
    with create_pipe_input() as pipe_input:
        runtime = TuiRuntime(input_obj=pipe_input, output_obj=DummyOutput())

        with pytest.MonkeyPatch.context() as monkeypatch:
            monkeypatch.setattr(
                runtime.screen.application.output,
                "get_size",
                lambda: Size(rows=24, columns=80),
            )
            await runtime.open()
            try:
                with patch.object(
                    runtime.screen.input.buffer,
                    "start_completion",
                    wraps=runtime.screen.input.buffer.start_completion,
                ) as start_completion:
                    pipe_input.send_text("/")
                    await wait_for_completion(runtime)

                start_completion.assert_not_called()
                runtime.screen.application.invalidate()
                await asyncio.sleep(0)

                buffer = runtime.screen.input.buffer
                assert buffer.text == "/"
                assert buffer.suggestion is None
                assert buffer.complete_state is not None
                assert buffer.complete_state.complete_index == 0
                assert rendered_input_line(runtime) == "› /"
            finally:
                await runtime.close()


@pytest.mark.anyio
async def test_slash_completion_only_opens_on_the_first_input_line() -> None:
    with create_pipe_input() as pipe_input:
        runtime = TuiRuntime(input_obj=pipe_input, output_obj=DummyOutput())

        await runtime.open()
        try:
            pipe_input.send_text("/")
            await wait_for_completion(runtime)

            pipe_input.send_text("\n/")
            await wait_for_input_text(runtime, "/\n/")
            await asyncio.sleep(0)

            buffer = runtime.screen.input.buffer
            assert buffer.complete_state is None
            assert not runtime.screen._completion_visible()
            assert runtime.input_model.completion_menu_completions(
                buffer.document
            ) is None
        finally:
            await runtime.close()


def test_slash_suggestion_is_not_shown_on_any_input_line() -> None:
    runtime = TuiRuntime()
    buffer = runtime.screen.input.buffer

    buffer.document = Document("/model\ndraft", cursor_position=6)
    assert runtime.input_model.auto_suggest.get_suggestion(
        buffer,
        buffer.document,
    ) is None

    buffer.document = Document("draft\n/model", cursor_position=12)
    assert runtime.input_model.auto_suggest.get_suggestion(
        buffer,
        buffer.document,
    ) is None


@pytest.mark.anyio
async def test_ctrl_j_closes_skill_menu_and_restores_natural_height() -> None:
    with create_pipe_input() as pipe_input:
        runtime = TuiRuntime(input_obj=pipe_input, output_obj=DummyOutput())
        runtime.input_model.set_skills(tuple(
            skill_spec(f"skill-{index:02d}")
            for index in range(12)
        ))

        with patch.object(
            runtime.screen.application.output,
            "get_size",
            return_value=Size(rows=24, columns=40),
        ):
            await runtime.open()
            try:
                pipe_input.send_text("$")
                await wait_for_completion(runtime)
                await render_next_frame(runtime)

                assert runtime.screen._completion_section_height() == 10

                pipe_input.send_text("\n")
                await wait_for_input_text(runtime, "$\n")
                screen = await render_next_frame(runtime)
                positions = screen.visible_windows_to_write_positions

                assert runtime.screen.input.buffer.complete_state is None
                assert not runtime.screen._completion_visible()
                assert runtime.screen._completion_section_height() == 0
                assert runtime.screen._visible_height() == (
                    runtime.screen._natural_visible_height()
                )
                assert runtime.screen.canvas_spacer not in positions
            finally:
                await runtime.close()


@pytest.mark.anyio
async def test_forward_typing_never_leaves_a_blank_completion_frame() -> None:
    with create_pipe_input() as pipe_input:
        runtime = TuiRuntime(input_obj=pipe_input, output_obj=DummyOutput())
        snapshots: list[tuple[str, bool]] = []

        def capture_completion_state(buffer) -> None:
            snapshots.append((
                buffer.text,
                bool(
                    runtime.screen._native_completion_visible()
                    or runtime.screen._completion_fallback_visible()
                ),
            ))

        runtime.screen.input.buffer.on_text_insert += capture_completion_state

        await runtime.open()
        try:
            pipe_input.send_text("/permissions")
            await wait_for_input_text(runtime, "/permissions")

            assert snapshots
            assert all(visible for _text, visible in snapshots)
        finally:
            await runtime.close()


@pytest.mark.anyio
async def test_slash_canvas_frames_restore_current_layout_after_dismissal(
) -> None:
    with create_pipe_input() as pipe_input:
        runtime = TuiRuntime(input_obj=pipe_input, output_obj=DummyOutput())
        frames: list[tuple[str, int, int, bool]] = []

        def capture_frame(_application) -> None:
            screen = runtime.screen.application.renderer.last_rendered_screen
            positions = screen.visible_windows_to_write_positions
            input_position = positions.get(runtime.screen.input.window)
            if input_position is None:
                return None

            input_row = (
                24 - runtime.screen._visible_height() + input_position.ypos
            )
            has_rendered_candidate = any(
                "".join(
                    cells[column].char
                    for column in sorted(cells)
                ).lstrip().startswith("/")
                for row, cells in screen.data_buffer.items()
                if row > input_position.ypos
            )
            frames.append((
                runtime.screen.input.buffer.text,
                input_row,
                runtime.screen._completion_section_height(),
                has_rendered_candidate,
            ))

        with patch.object(
            runtime.screen.application.output,
            "get_size",
            return_value=Size(rows=24, columns=40),
        ):
            runtime.screen.application.after_render += capture_frame
            await runtime.open()
            try:
                idle_screen = (
                    runtime.screen.application.renderer.last_rendered_screen
                )
                idle_position = (
                    idle_screen.visible_windows_to_write_positions[
                        runtime.screen.input.window
                    ]
                )
                idle_row = (
                    24 - runtime.screen._visible_height() + idle_position.ypos
                )

                opened_at = len(frames)
                pipe_input.send_text("/")
                await wait_for_completion(runtime)
                await render_next_frame(runtime)

                opened_frames = [
                    frame
                    for frame in frames[opened_at:]
                    if frame[0] == "/"
                ]
                assert idle_row == 21
                assert opened_frames
                assert all(
                    input_row == 14
                    and completion_height == 8
                    and has_rendered_candidate
                    for (
                        _text,
                        input_row,
                        completion_height,
                        has_rendered_candidate,
                    ) in opened_frames
                ), opened_frames

                dismissed_at = len(frames)
                runtime.input_model.dismiss_completion_menu(
                    runtime.screen.input.buffer
                )
                await render_next_frame(runtime)

                dismissed_frames = frames[dismissed_at:]
                assert dismissed_frames
                assert all(
                    input_row == idle_row
                    and completion_height == 0
                    and not has_rendered_candidate
                    for (
                        _text,
                        input_row,
                        completion_height,
                        has_rendered_candidate,
                    ) in dismissed_frames
                ), dismissed_frames
            finally:
                await runtime.close()


@pytest.mark.anyio
@pytest.mark.parametrize("prefix", ("/", "$"))
@pytest.mark.parametrize(
    "clear_method",
    ("ctrl_w", "backspace", "delete"),
)
async def test_clearing_multiline_completion_collapses_canvas(
    prefix: str,
    clear_method: str,
) -> None:
    with create_pipe_input() as pipe_input:
        runtime = TuiRuntime(input_obj=pipe_input, output_obj=DummyOutput())
        runtime.input_model.set_skills(tuple(
            skill_spec(f"skill-{index:02d}")
            for index in range(12)
        ))

        with patch.object(
            runtime.screen.application.output,
            "get_size",
            return_value=Size(rows=24, columns=40),
        ):
            await runtime.open()
            try:
                buffer = runtime.screen.input.buffer
                text = prefix + "\n" * 12
                buffer.document = Document(text, cursor_position=len(prefix))
                runtime.input_model.refresh_completion_menu(buffer)
                await wait_for_input_text(runtime, text)
                await wait_for_completion(runtime)
                expanded = await render_next_frame(runtime)

                assert expanded.height == 24
                assert runtime.screen._input_height() == 13
                assert runtime.screen._completion_section_height() == 8

                buffer.cursor_position = len(buffer.text)
                if clear_method == "ctrl_w":
                    pipe_input.send_text("\x17" * 13)
                elif clear_method == "delete":
                    runtime.screen.input.buffer.cursor_position = 0
                    pipe_input.send_text("\x1b[3~" * 13)
                else:
                    pipe_input.send_text("\x7f" * 13)
                await wait_for_input_text(runtime, "")
                collapsed = await render_next_frame(runtime)
                positions = collapsed.visible_windows_to_write_positions

                assert runtime.screen._input_height() == 1
                assert runtime.screen._completion_section_height() == 0
                assert runtime.screen._visible_height() == (
                    runtime.screen._natural_visible_height()
                )
                assert runtime.screen._visible_height() < expanded.height
                assert runtime.screen.canvas_spacer not in positions
            finally:
                await runtime.close()


@pytest.mark.anyio
@pytest.mark.parametrize("stable_line_count", (0, 20))
@pytest.mark.parametrize("close_method", ("escape", "backspace"))
async def test_dismissed_slash_completion_keeps_fixed_outer_inset(
    close_method: str,
    stable_line_count: int,
) -> None:
    with create_pipe_input() as pipe_input:
        runtime = TuiRuntime(input_obj=pipe_input, output_obj=DummyOutput())

        with patch.object(
            runtime.screen.application.output,
            "get_size",
            return_value=Size(rows=24, columns=40),
        ):
            await runtime.open()
            try:
                if stable_line_count:
                    runtime.append_block(
                        FragmentBlock(((
                            "",
                            "\n".join(
                                f"stable {index}"
                                for index in range(stable_line_count)
                            ),
                        ),)),
                        kind="assistant",
                    )
                    await render_next_frame(runtime)

                runtime.set_execution_active(True)
                runtime.set_active_renderable(
                    FragmentBlock(((
                        "",
                        "\n".join(f"stream {index}" for index in range(6)),
                    ),)),
                    kind="assistant",
                )
                await render_next_frame(runtime)

                screen = await render_next_frame(runtime)
                positions = screen.visible_windows_to_write_positions
                transcript = positions[runtime.screen.transcript_window]
                outer_inset = positions[
                    runtime.screen.bottom_pane_top_inset.content
                ]
                top_padding = positions[runtime.screen.input_top_padding]
                input_position = positions[runtime.screen.input.window]
                before = (
                    24
                    - runtime.screen._visible_height()
                    + input_position.ypos
                )

                assert outer_inset.height == 1
                assert outer_inset.ypos == (
                    transcript.ypos + transcript.height
                )
                assert top_padding.ypos == outer_inset.ypos + 1
                assert input_position.ypos == top_padding.ypos + 1

                pipe_input.send_text("/")
                await wait_for_completion(runtime)
                screen = await render_next_frame(runtime)
                positions = screen.visible_windows_to_write_positions
                raised = screen.visible_windows_to_write_positions[
                    runtime.screen.input.window
                ]
                raised_row = (
                    24
                    - runtime.screen._visible_height()
                    + raised.ypos
                )

                assert runtime.screen.canvas_spacer not in positions
                assert (
                    runtime.screen.bottom_pane_top_inset.content
                    in positions
                )

                if close_method == "escape":
                    runtime.input_model.dismiss_completion_menu(
                        runtime.screen.input.buffer
                    )
                else:
                    pipe_input.send_text("\x7f")
                    await wait_for_input_text(runtime, "")

                screen = await render_next_frame(runtime)
                positions = screen.visible_windows_to_write_positions
                transcript = positions[runtime.screen.transcript_window]
                outer_inset = positions[
                    runtime.screen.bottom_pane_top_inset.content
                ]
                top_padding = positions[runtime.screen.input_top_padding]
                dismissed = positions[runtime.screen.input.window]
                dismissed_row = (
                    24
                    - runtime.screen._visible_height()
                    + dismissed.ypos
                )

                assert raised_row < before
                assert dismissed_row == before
                assert runtime.screen.canvas_spacer not in positions
                assert outer_inset.ypos == (
                    transcript.ypos + transcript.height
                )
                assert top_padding.ypos == outer_inset.ypos + 1
                assert dismissed.ypos == top_padding.ypos + top_padding.height

                final_block = None
                for line_count in range(7, 15):
                    final_block = FragmentBlock(((
                        "",
                        "\n".join(
                            f"stream {index}"
                            for index in range(line_count)
                        ),
                    ),))
                    runtime.set_active_renderable(
                        final_block,
                        kind="assistant",
                    )
                    screen = await render_next_frame(runtime)
                    positions = screen.visible_windows_to_write_positions
                    transcript = positions[runtime.screen.transcript_window]
                    outer_inset = positions[
                        runtime.screen.bottom_pane_top_inset.content
                    ]
                    top_padding = positions[runtime.screen.input_top_padding]
                    input_position = positions[runtime.screen.input.window]
                    assert runtime.screen._visible_height() == (
                        runtime.screen._natural_visible_height()
                    )
                    assert runtime.screen.canvas_spacer not in positions
                    assert outer_inset.ypos == (
                        transcript.ypos + transcript.height
                    )
                    assert top_padding.ypos == outer_inset.ypos + 1
                    assert input_position.ypos == (
                        top_padding.ypos + top_padding.height
                    )

                assert final_block is not None
                runtime.commit_active_renderable(final_block)
                screen = await render_next_frame(runtime)
                positions = screen.visible_windows_to_write_positions
                committed = positions[
                    runtime.screen.input.window
                ]
                committed_row = (
                    24
                    - runtime.screen._visible_height()
                    + committed.ypos
                )

                assert runtime.screen._visible_height() == (
                    runtime.screen._natural_visible_height()
                )
                assert runtime.screen.canvas_spacer not in positions
            finally:
                runtime.set_execution_active(False)
                await runtime.close()


@pytest.mark.anyio
@pytest.mark.parametrize("stabilize_prefix", (False, True))
async def test_stream_growth_keeps_outer_inset_after_completion_closes(
    stabilize_prefix: bool,
) -> None:
    with create_pipe_input() as pipe_input:
        runtime = TuiRuntime(input_obj=pipe_input, output_obj=DummyOutput())

        with patch.object(
            runtime.screen.application.output,
            "get_size",
            return_value=Size(rows=24, columns=40),
        ):
            await runtime.open()
            try:
                runtime.append_block(
                    FragmentBlock((("", "query"),)),
                    kind="user",
                )
                await render_next_frame(runtime)

                runtime.set_execution_active(True)
                runtime.set_active_renderable(
                    FragmentBlock(((
                        "",
                        "\n".join(f"stream {index}" for index in range(6)),
                    ),)),
                    kind="assistant",
                )
                await render_next_frame(runtime)

                pipe_input.send_text("/")
                await wait_for_completion(runtime)
                await render_next_frame(runtime)

                if stabilize_prefix:
                    prefix = "\n".join(
                        f"stream {index}" for index in range(8)
                    )
                    runtime.commit_active_stream_prefix(
                        FragmentBlock((("", prefix),)),
                        raw_text=prefix,
                    )
                    tail = "\n".join(
                        f"stream {index}" for index in range(8, 14)
                    )
                    runtime.set_active_renderable(
                        FragmentBlock((("", tail),)),
                        kind="assistant",
                        raw_text=tail,
                        stream_continuation=True,
                    )
                else:
                    runtime.set_active_renderable(
                        FragmentBlock(((
                            "",
                            "\n".join(
                                f"stream {index}" for index in range(14)
                            ),
                        ),)),
                        kind="assistant",
                    )
                await render_next_frame(runtime)

                pipe_input.send_text("\x7f")
                await wait_for_input_text(runtime, "")
                screen = await render_next_frame(runtime)
                positions = screen.visible_windows_to_write_positions
                transcript = positions[runtime.screen.transcript_window]
                outer_inset = positions[
                    runtime.screen.bottom_pane_top_inset.content
                ]
                top_padding = positions[runtime.screen.input_top_padding]
                input_position = positions[runtime.screen.input.window]
                assert runtime.screen._visible_height() == (
                    runtime.screen._natural_visible_height()
                )
                assert runtime.screen.canvas_spacer not in positions
                assert outer_inset.ypos == (
                    transcript.ypos + transcript.height
                )
                assert top_padding.ypos == outer_inset.ypos + 1

                if stabilize_prefix:
                    tail = "\n".join(
                        f"stream {index}" for index in range(8, 15)
                    )
                    runtime.set_active_renderable(
                        FragmentBlock((("", tail),)),
                        kind="assistant",
                        raw_text=tail,
                        stream_continuation=True,
                    )
                else:
                    runtime.set_active_renderable(
                        FragmentBlock(((
                            "",
                            "\n".join(
                                f"stream {index}" for index in range(15)
                            ),
                        ),)),
                        kind="assistant",
                    )
                screen = await render_next_frame(runtime)
                positions = screen.visible_windows_to_write_positions
                transcript = positions[runtime.screen.transcript_window]
                outer_inset = positions[
                    runtime.screen.bottom_pane_top_inset.content
                ]
                top_padding = positions[runtime.screen.input_top_padding]
                next_input = positions[runtime.screen.input.window]
                assert runtime.screen._visible_height() == (
                    runtime.screen._natural_visible_height()
                )
                assert runtime.screen.canvas_spacer not in positions
                assert outer_inset.ypos == (
                    transcript.ypos + transcript.height
                )
                assert top_padding.ypos == outer_inset.ypos + 1
                assert next_input.ypos == (
                    top_padding.ypos + top_padding.height
                )
            finally:
                runtime.set_execution_active(False)
                await runtime.close()


@pytest.mark.anyio
async def test_slash_completion_does_not_commit_active_stream() -> None:
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
                runtime.set_execution_active(True)
                await output.append_assistant_delta("\n".join(
                    f"line {index:02d}" for index in range(40)
                ))
                await render_next_frame(runtime)

                pipe_input.send_text("/")
                await wait_for_completion(runtime)
                await render_next_frame(runtime)

                await output.append_assistant_delta("\n" + "\n".join(
                    f"line {index:02d}" for index in range(40, 60)
                ))
                await render_next_frame(runtime)
                await asyncio.sleep(0.02)

                assert runtime.document.scrollback_line_count == 0
                assert not runtime.document.blocks

                pipe_input.send_text("\x7f")
                await wait_for_input_text(runtime, "")
                await render_next_frame(runtime)

                await asyncio.sleep(0.02)
                assert runtime.document.scrollback_line_count == 0
                assert not runtime.document.blocks

                await output.prepare_external_output()
                runtime.set_execution_active(False)

                for _ in range(50):
                    await asyncio.sleep(0.002)
                    if runtime.document.scrollback_line_count > 0:
                        break

                assert runtime.document.scrollback_line_count > 0
            finally:
                runtime.set_execution_active(False)
                await runtime.close()


@pytest.mark.anyio
async def test_streaming_status_keeps_internal_interaction_gap() -> None:
    with create_pipe_input() as pipe_input:
        runtime = TuiRuntime(input_obj=pipe_input, output_obj=DummyOutput())

        with patch.object(
            runtime.screen.application.output,
            "get_size",
            return_value=Size(rows=12, columns=40),
        ):
            await runtime.open()
            try:
                runtime.set_execution_active(True)
                runtime.set_active_renderable(
                    FragmentBlock((("", "streaming answer"),)),
                    kind="assistant",
                )
                runtime.screen.set_activity_renderable(
                    FragmentBlock((("", "Thinking"),))
                )

                screen = await render_next_frame(runtime)
                positions = screen.visible_windows_to_write_positions
                status = positions[runtime.screen.status_window]
                content_gap = positions[
                    runtime.screen.status_interaction_gap.content
                ]
                top_padding = positions[runtime.screen.input_top_padding]

                assert content_gap.ypos == status.ypos + status.height
                assert top_padding.ypos == (
                    content_gap.ypos + content_gap.height
                )
            finally:
                runtime.screen.clear_activity_renderable()
                runtime.set_execution_active(False)
                await runtime.close()


@pytest.mark.anyio
@pytest.mark.parametrize("wait_for_candidates", (False, True))
@pytest.mark.parametrize("stable_line_count", (0, 20))
async def test_streaming_slash_completion_has_outer_and_inner_insets(
    wait_for_candidates: bool,
    stable_line_count: int,
) -> None:
    with create_pipe_input() as pipe_input:
        runtime = TuiRuntime(input_obj=pipe_input, output_obj=DummyOutput())

        with patch.object(
            runtime.screen.application.output,
            "get_size",
            return_value=Size(rows=24, columns=40),
        ):
            await runtime.open()
            try:
                if stable_line_count:
                    runtime.append_block(
                        FragmentBlock(((
                            "",
                            "\n".join(
                                f"stable {index}"
                                for index in range(stable_line_count)
                            ),
                        ),)),
                        kind="assistant",
                    )
                    await render_next_frame(runtime)

                runtime.set_execution_active(True)
                runtime.set_active_renderable(
                    FragmentBlock((("", "streaming response"),)),
                    kind="assistant",
                )
                await render_next_frame(runtime)

                pipe_input.send_text("/")
                if wait_for_candidates:
                    await wait_for_completion(runtime)
                else:
                    await wait_for_input_text(runtime, "/")

                runtime.set_active_renderable(
                    FragmentBlock((("", "streaming response updated"),)),
                    kind="assistant",
                )
                screen = await render_next_frame(runtime)
                positions = screen.visible_windows_to_write_positions
                transcript = positions[runtime.screen.transcript_window]
                outer_inset = positions[
                    runtime.screen.bottom_pane_top_inset.content
                ]
                top_padding = positions[runtime.screen.input_top_padding]
                input_position = positions[runtime.screen.input.window]

                assert runtime.screen._completion_visible()
                assert outer_inset.ypos == (
                    transcript.ypos + transcript.height
                )
                assert top_padding.ypos == outer_inset.ypos + 1
                assert input_position.ypos == top_padding.ypos + 1
            finally:
                runtime.set_execution_active(False)
                await runtime.close()


@pytest.mark.anyio
@pytest.mark.parametrize("stable_line_count", (0, 20))
@pytest.mark.parametrize("surface", ("approval", "menu"))
async def test_bottom_surface_restores_streaming_input_and_focus(
    surface: str,
    stable_line_count: int,
) -> None:
    with create_pipe_input() as pipe_input:
        runtime = TuiRuntime(input_obj=pipe_input, output_obj=DummyOutput())

        with patch.object(
            runtime.screen.application.output,
            "get_size",
            return_value=Size(rows=24, columns=40),
        ):
            await runtime.open()
            surface_task = None
            try:
                if stable_line_count:
                    runtime.append_block(
                        FragmentBlock(((
                            "",
                            "\n".join(
                                f"stable {index}"
                                for index in range(stable_line_count)
                            ),
                        ),)),
                        kind="assistant",
                    )
                    await render_next_frame(runtime)

                runtime.set_execution_active(True)
                runtime.set_active_renderable(
                    FragmentBlock(((
                        "",
                        "\n".join(f"stream {index}" for index in range(8)),
                    ),)),
                    kind="assistant",
                )
                await render_next_frame(runtime)

                pipe_input.send_text("/")
                await wait_for_completion(runtime)
                slash_screen = await render_next_frame(runtime)
                assert runtime.screen.input.window in (
                    slash_screen.visible_windows_to_write_positions
                )

                if surface == "approval":
                    surface_task = asyncio.create_task(
                        ApprovalCoordinator(runtime).request({
                            "tool": "shell_command",
                            "command": "\n".join(
                                f"echo line-{index}"
                                for index in range(30)
                            ),
                            "show_timer": False,
                        })
                    )
                    surface_active = (
                        lambda: runtime.screen.approval.state is not None
                    )
                elif surface == "menu":
                    surface_task = asyncio.create_task(runtime.select_menu(
                        MenuRequest(
                            title="Menu",
                            options=tuple(
                                MenuOption(index, f"Option {index}")
                                for index in range(20)
                            ),
                        ),
                    ))
                    surface_active = lambda: runtime.screen.menu.active
                for _ in range(20):
                    await asyncio.sleep(0)
                    if surface_active():
                        break
                assert surface_active()
                await render_next_frame(runtime)
                assert 1 <= runtime.screen._visible_height() <= 24

                if surface == "approval":
                    runtime.screen.approval.finish("accept")
                    assert await surface_task == "accept"
                elif surface == "menu":
                    runtime.screen.menu.finish("done")
                    assert await surface_task == "done"
                surface_task = None

                screen = await render_next_frame(runtime)
                positions = screen.visible_windows_to_write_positions
                assert runtime.screen.input.window in positions
                assert runtime.screen.bottom_pane.input_visible
                assert runtime.screen.input.buffer.text == "/"
                assert runtime.screen.application.layout.current_window is (
                    runtime.screen.input.window
                )
                assert "stream 7" in fragments_text(
                    runtime.document.live_fragments()
                )
                if stable_line_count:
                    assert runtime.document.scrollback_line_count > 0
            finally:
                if runtime.screen.approval.state is not None:
                    runtime.screen.approval.finish("decline")
                if runtime.screen.menu.active:
                    runtime.screen.menu.finish(None)
                if surface_task is not None:
                    await surface_task
                runtime.set_execution_active(False)
                await runtime.close()
