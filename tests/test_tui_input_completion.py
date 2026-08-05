# -*- coding: utf-8 -*-

import asyncio
from pathlib import Path
from unittest.mock import patch

import pytest
from prompt_toolkit.completion import Completion
from prompt_toolkit.data_structures import Size
from prompt_toolkit.document import Document
from prompt_toolkit.input.defaults import create_pipe_input
from prompt_toolkit.output import DummyOutput

from mind_core.skills import SkillSpec
from mind_app.interaction.contracts import PromptContext
from mind_app.tui.core.models import (
    FragmentBlock,
    MenuOption,
    MenuRequest,
)
from mind_app.tui.core.process_viewer import ProcessViewerRequest
from mind_app.tui.core.runtime import TuiRuntime


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


async def wait_for_suggestion(runtime: TuiRuntime) -> None:
    """等待当前输入对应的行内联想就绪。"""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + 1.0
    while loop.time() < deadline:
        if runtime.screen.input.buffer.suggestion is not None:
            return
        await asyncio.sleep(0.001)
    raise AssertionError("suggestion did not become ready")


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


async def wait_for_submission(runtime: TuiRuntime):
    """等待输入处理结果进入提交队列。"""
    return await asyncio.wait_for(
        runtime.submissions.read_submission(),
        timeout=1.0,
    )


@pytest.mark.anyio
async def test_bracketed_paste_sanitizes_control_characters() -> None:
    with create_pipe_input() as pipe_input:
        runtime = TuiRuntime(input_obj=pipe_input, output_obj=DummyOutput())

        await runtime.open()
        try:
            pipe_input.send_text(
                "\x1b[200~BugID\t提交时间\r\n. 3117\t已解决\x00\x1b[201~"
            )
            await wait_for_input_text(
                runtime,
                "BugID   提交时间\n. 3117  已解决",
            )
        finally:
            await runtime.close()


def rendered_input_line(runtime: TuiRuntime) -> str:
    """返回最近一次渲染中的首行输入文本。"""
    screen = runtime.screen.application.renderer.last_rendered_screen
    position = screen.visible_windows_to_write_positions[
        runtime.screen.input.window
    ]
    row = screen.data_buffer[position.ypos]
    return "".join(
        row[column].char
        for column in range(position.xpos, position.xpos + position.width)
    ).rstrip()


def rendered_window_line(runtime: TuiRuntime, window) -> str:
    """返回指定窗口最近一次渲染的首行文本。"""
    screen = runtime.screen.application.renderer.last_rendered_screen
    position = screen.visible_windows_to_write_positions[window]
    row = screen.data_buffer[position.ypos]
    return "".join(
        row[column].char
        for column in range(position.xpos + position.width)
    ).rstrip()


def skill_spec(name: str) -> SkillSpec:
    """创建输入补全测试使用的 skill 描述。"""
    entry = Path(f"{name}/SKILL.md")
    return SkillSpec(
        name=name,
        description=f"Use {name}",
        source="test",
        root=entry.parent,
        entry=entry,
    )


def test_command_completion_discards_text_after_cursor() -> None:
    runtime = TuiRuntime()
    buffer  = runtime.screen.input.buffer
    buffer.document = Document("/mc xxxx", cursor_position=3)

    runtime.input_model.apply_completion(
        buffer,
        Completion("/mcp", start_position=-3),
    )

    assert buffer.text == "/mcp"
    assert buffer.cursor_position == len("/mcp")


def test_completion_surface_has_no_async_footer_gap() -> None:
    runtime = TuiRuntime()
    buffer  = runtime.screen.input.buffer
    buffer.document = Document("/mc", cursor_position=3)

    assert buffer.complete_state is None
    assert runtime.screen._completion_visible()
    assert runtime.screen._completion_height() == 1
    assert runtime.screen._completion_section_height() == 1
    assert (
        runtime.screen._input_stack_height()
        == runtime.screen._input_surface_height() + 1
    )
    assert not runtime.screen._footer_visible()

    buffer.document = Document("/mcp", cursor_position=4)

    assert runtime.screen._completion_visible()
    assert runtime.screen._completion_height() == 1
    assert not runtime.screen._footer_visible()
    assert "".join(
        text
        for _style, text in runtime.screen._completion_fallback_fragments()
    ).lstrip().startswith("/mcp")


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
@pytest.mark.parametrize("stable_line_count", (0, 20))
@pytest.mark.parametrize("close_method", ("escape", "backspace"))
async def test_dismissed_slash_completion_tracks_stream_without_top_spacer(
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
                input_position = positions[runtime.screen.input.window]
                before = (
                    24
                    - runtime.screen._visible_height()
                    + input_position.ypos
                )

                assert runtime.screen.content_input_gap.content not in positions
                assert input_position.ypos == (
                    transcript.ypos + transcript.height + 1
                )

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
                assert runtime.screen.content_input_gap.content not in positions

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
                top_padding = positions[runtime.screen.input_top_padding]
                dismissed = positions[runtime.screen.input.window]
                dismissed_row = (
                    24
                    - runtime.screen._visible_height()
                    + dismissed.ypos
                )

                assert raised_row < before
                assert dismissed_row == raised_row
                assert runtime.screen.canvas_spacer not in positions
                assert runtime.screen.content_input_gap.content not in positions
                assert top_padding.ypos == (
                    transcript.ypos + transcript.height
                )
                assert dismissed.ypos == top_padding.ypos + top_padding.height

                initial_release = runtime.screen._bottom_release_height()
                final_block = None
                for growth in range(1, initial_release + 1):
                    line_count = 6 + growth
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
                    top_padding = positions[runtime.screen.input_top_padding]
                    input_position = positions[runtime.screen.input.window]
                    input_row = (
                        24
                        - runtime.screen._visible_height()
                        + input_position.ypos
                    )

                    assert runtime.screen.canvas_spacer not in positions
                    assert (
                        runtime.screen.content_input_gap.content
                        not in positions
                    )
                    assert top_padding.ypos == (
                        transcript.ypos + transcript.height
                    )
                    assert input_position.ypos == (
                        top_padding.ypos + top_padding.height
                    )
                    assert input_row == min(
                        before,
                        dismissed_row + growth,
                    )
                    assert runtime.screen._bottom_release_height() == (
                        initial_release - growth
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

                assert committed_row == before
                assert runtime.screen.canvas_spacer not in positions
            finally:
                runtime.set_execution_active(False)
                await runtime.close()


@pytest.mark.anyio
async def test_streaming_status_keeps_content_input_gap() -> None:
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
                    runtime.screen.content_input_gap.content
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
async def test_streaming_slash_completion_has_one_row_above_input(
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
                input_position = positions[runtime.screen.input.window]

                assert runtime.screen._completion_visible()
                assert input_position.ypos == (
                    transcript.ypos + transcript.height + 1
                )
                assert runtime.screen.content_input_gap.content not in positions
            finally:
                runtime.set_execution_active(False)
                await runtime.close()


@pytest.mark.anyio
@pytest.mark.parametrize("stable_line_count", (0, 20))
@pytest.mark.parametrize(
    (
        "surface",
        "surface_visible_height",
        "closed_visible_height",
        "expected_release_height",
    ),
    (
        ("approval", 24, 24, 5),
        ("menu", 22, 21, 2),
        ("process_viewer", 24, 23, 4),
    ),
)
async def test_bottom_surface_release_preserves_streaming_slash_anchor(
    surface: str,
    surface_visible_height: int,
    closed_visible_height: int,
    expected_release_height: int,
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
                slash_position = (
                    slash_screen.visible_windows_to_write_positions[
                        runtime.screen.input.window
                    ]
                )
                slash_row = (
                    24
                    - runtime.screen._visible_height()
                    + slash_position.ypos
                )
                assert slash_row == 14

                if surface == "approval":
                    surface_task = asyncio.create_task(
                        runtime.request_approval({
                            "tool": "shell_command",
                            "command": "\n".join(
                                f"echo line-{index}"
                                for index in range(30)
                            ),
                            "show_timer": False,
                        })
                    )
                    surface_active = lambda: runtime.screen.approval.active
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
                else:
                    surface_task = runtime.screen.process_viewer.begin(
                        ProcessViewerRequest(
                            fragments=((
                                "",
                                "\n".join(
                                    f"process line {index}"
                                    for index in range(30)
                                ),
                            ),),
                            max_height=28,
                        )
                    )
                    surface_active = (
                        lambda: runtime.screen.process_viewer.active
                    )

                for _ in range(20):
                    await asyncio.sleep(0)
                    if surface_active():
                        break
                assert surface_active()
                await render_next_frame(runtime)
                assert runtime.screen._visible_height() == (
                    24 if stable_line_count else surface_visible_height
                )

                if surface == "approval":
                    runtime.screen.approval.finish("accept")
                    assert await surface_task == "accept"
                elif surface == "menu":
                    runtime.screen.menu.finish("done")
                    assert await surface_task == "done"
                else:
                    runtime.screen.process_viewer.resolve("done")
                    assert await surface_task == "done"
                    runtime.screen.process_viewer.settle()
                surface_task = None

                screen = await render_next_frame(runtime)
                positions = screen.visible_windows_to_write_positions
                input_position = positions[runtime.screen.input.window]
                input_row = (
                    24
                    - runtime.screen._visible_height()
                    + input_position.ypos
                )
                release_height = runtime.screen._bottom_release_height()
                expected_closed_height = (
                    24 if stable_line_count else closed_visible_height
                )
                expected_release = (
                    0 if stable_line_count else expected_release_height
                )

                assert runtime.screen._visible_height() == (
                    expected_closed_height
                )
                assert release_height == expected_release
                assert input_row == slash_row - release_height
                assert runtime.screen.canvas_spacer not in positions
                assert (
                    runtime.screen.content_input_gap.content
                    not in positions
                )

                released_input_row = input_row
                growth_steps = max(2, release_height)
                for growth in range(1, growth_steps + 1):
                    line_count = 8 + growth
                    runtime.set_active_renderable(
                        FragmentBlock(((
                            "",
                            "\n".join(
                                f"stream {index}"
                                for index in range(line_count)
                            ),
                        ),)),
                        kind="assistant",
                    )
                    screen = await render_next_frame(runtime)
                    input_position = (
                        screen.visible_windows_to_write_positions[
                            runtime.screen.input.window
                        ]
                    )
                    input_row = (
                        24
                        - runtime.screen._visible_height()
                        + input_position.ypos
                    )

                    assert input_row == min(
                        slash_row,
                        released_input_row + growth,
                    )
                    assert runtime.screen._bottom_release_height() == max(
                        0,
                        release_height - growth,
                    )
                    assert runtime.screen.canvas_spacer not in (
                        screen.visible_windows_to_write_positions
                    )

                assert input_row == slash_row
                assert runtime.screen._bottom_release_height() == 0
            finally:
                if runtime.screen.approval.active:
                    runtime.screen.approval.finish("decline")
                if runtime.screen.menu.active:
                    runtime.screen.menu.finish(None)
                if runtime.screen.process_viewer.active:
                    runtime.screen.process_viewer.resolve("detach")
                    runtime.screen.process_viewer.settle()
                if surface_task is not None:
                    await surface_task
                runtime.set_execution_active(False)
                await runtime.close()


@pytest.mark.anyio
async def test_input_prompt_uses_single_space_before_placeholder() -> None:
    with create_pipe_input() as pipe_input:
        runtime = TuiRuntime(input_obj=pipe_input, output_obj=DummyOutput())
        runtime.submissions.placeholder_text = "Write tests for @filename"

        await runtime.open()
        try:
            runtime.screen.application.invalidate()
            await asyncio.sleep(0)

            assert rendered_input_line(runtime) == "›  Write tests for @filename"
        finally:
            await runtime.close()


@pytest.mark.anyio
async def test_inline_command_hint_keeps_leading_space() -> None:
    with create_pipe_input() as pipe_input:
        runtime = TuiRuntime(input_obj=pipe_input, output_obj=DummyOutput())

        await runtime.open()
        try:
            pipe_input.send_text("/model ")
            await wait_for_input_text(runtime, "/model ")
            await wait_for_suggestion(runtime)
            runtime.screen.application.invalidate()
            await asyncio.sleep(0)

            assert runtime.screen.input.buffer.suggestion is not None
            assert runtime.screen.input.buffer.suggestion.text == "<model-id>"
            assert rendered_input_line(runtime) == "› /model <model-id>"
        finally:
            await runtime.close()


@pytest.mark.anyio
async def test_slash_completion_aligns_with_input_command() -> None:
    with create_pipe_input() as pipe_input:
        runtime = TuiRuntime(input_obj=pipe_input, output_obj=DummyOutput())

        await runtime.open()
        try:
            pipe_input.send_text("/sk")
            await wait_for_completion(runtime)
            runtime.screen.application.invalidate()
            await asyncio.sleep(0)

            input_line = rendered_input_line(runtime)
            menu_window = runtime.screen.completion_menu.content
            completion_line = rendered_window_line(runtime, menu_window)
            screen = runtime.screen.application.renderer.last_rendered_screen
            bottom_position = screen.visible_windows_to_write_positions[
                runtime.screen.input_bottom_padding
            ]
            menu_position = screen.visible_windows_to_write_positions[menu_window]

            assert input_line == "› /sk"
            assert completion_line.lstrip().startswith("/skills")
            assert input_line.index("/") == completion_line.index("/")
            assert menu_position.xpos == input_line.index("/") - 1
            assert "class:completion-menu.completion.current" in (
                screen.data_buffer[menu_position.ypos][menu_position.xpos].style
            )
            meta_column = input_line.index("/") + len("/skills") + 1
            assert "class:completion-menu.meta.completion.current" in (
                screen.data_buffer[menu_position.ypos][meta_column].style
            )
            assert menu_position.ypos == bottom_position.ypos + 1
        finally:
            await runtime.close()


@pytest.mark.anyio
async def test_exact_slash_completion_aligns_with_input_command() -> None:
    with create_pipe_input() as pipe_input:
        runtime = TuiRuntime(input_obj=pipe_input, output_obj=DummyOutput())

        await runtime.open()
        try:
            pipe_input.send_text("/skills")
            await wait_for_input_text(runtime, "/skills")
            runtime.screen.application.invalidate()
            await asyncio.sleep(0)

            input_line = rendered_input_line(runtime)
            completion_line = rendered_window_line(
                runtime,
                runtime.screen.completion_fallback_window,
            )
            screen = runtime.screen.application.renderer.last_rendered_screen
            fallback_position = screen.visible_windows_to_write_positions[
                runtime.screen.completion_fallback_window
            ]

            assert input_line == "› /skills"
            assert completion_line.lstrip().startswith("/skills")
            assert input_line.index("/") == completion_line.index("/")
            assert fallback_position.xpos == input_line.index("/") - 1
            assert "class:completion-menu.completion.current" in (
                screen.data_buffer[fallback_position.ypos][
                    fallback_position.xpos
                ].style
            )
            meta_column = input_line.index("/") + len("/skills") + 1
            assert "class:completion-menu.meta.completion.current" in (
                screen.data_buffer[fallback_position.ypos][meta_column].style
            )
        finally:
            await runtime.close()


@pytest.mark.anyio
async def test_slash_prefix_selects_first_match_without_rewriting_input() -> None:
    with create_pipe_input() as pipe_input:
        runtime = TuiRuntime(input_obj=pipe_input, output_obj=DummyOutput())

        await runtime.open()
        try:
            pipe_input.send_text("/f")
            await wait_for_completion(runtime)

            buffer = runtime.screen.input.buffer
            assert buffer.text == "/f"
            assert buffer.complete_state is not None
            assert buffer.complete_state.complete_index == 0
            assert buffer.complete_state.current_completion.display_text == "/fork"
        finally:
            await runtime.close()


@pytest.mark.anyio
async def test_tab_completes_selected_slash_command_without_submitting() -> None:
    with create_pipe_input() as pipe_input:
        runtime = TuiRuntime(input_obj=pipe_input, output_obj=DummyOutput())

        await runtime.open()
        try:
            pipe_input.send_text("/f")
            await wait_for_completion(runtime)
            pipe_input.send_text("\t")
            await wait_for_input_text(runtime, "/fork")

            assert runtime.submissions.message_queue.empty()
            assert runtime.screen._completion_fallback_visible()
            assert "".join(
                text
                for _style, text in runtime.screen._completion_fallback_fragments()
            ).lstrip().startswith("/fork")
        finally:
            await runtime.close()


@pytest.mark.anyio
async def test_enter_executes_the_default_root_command() -> None:
    with create_pipe_input() as pipe_input:
        runtime = TuiRuntime(input_obj=pipe_input, output_obj=DummyOutput())

        await runtime.open()
        try:
            pipe_input.send_text("/\r")
            submission = await wait_for_submission(runtime)

            assert submission.value == "/new"
            assert runtime.screen.input.buffer.text == ""
        finally:
            await runtime.close()


@pytest.mark.anyio
async def test_enter_opens_parameter_input_for_complete_command() -> None:
    with create_pipe_input() as pipe_input:
        runtime = TuiRuntime(input_obj=pipe_input, output_obj=DummyOutput())

        await runtime.open()
        try:
            pipe_input.send_text("/model\r")
            await wait_for_input_text(runtime, "/model ")

            assert runtime.submissions.message_queue.empty()
            assert not runtime.screen._completion_visible()
            assert runtime.screen.input.buffer.suggestion is not None
            assert runtime.screen.input.buffer.suggestion.text == "<model-id>"
        finally:
            await runtime.close()


@pytest.mark.anyio
async def test_unknown_slash_command_renders_non_selectable_empty_state() -> None:
    with create_pipe_input() as pipe_input:
        runtime = TuiRuntime(input_obj=pipe_input, output_obj=DummyOutput())

        await runtime.open()
        try:
            pipe_input.send_text("/aaa")
            await wait_for_input_text(runtime, "/aaa")
            runtime.screen.application.invalidate()
            await asyncio.sleep(0)

            buffer = runtime.screen.input.buffer
            assert buffer.complete_state is None
            assert runtime.screen._completion_fallback_visible()
            assert runtime.screen._completion_fallback_fragments() == [
                ("class:completion-menu.empty", " no matches"),
            ]
            assert rendered_window_line(
                runtime,
                runtime.screen.completion_fallback_window,
            ).lstrip() == "no matches"
        finally:
            await runtime.close()


@pytest.mark.anyio
async def test_skill_prefix_selects_first_match_without_rewriting_input() -> None:
    with create_pipe_input() as pipe_input:
        runtime = TuiRuntime(input_obj=pipe_input, output_obj=DummyOutput())
        runtime.input_model.set_skills((skill_spec("alpha"), skill_spec("beta")))

        await runtime.open()
        try:
            pipe_input.send_text("$")
            await wait_for_completion(runtime)

            buffer = runtime.screen.input.buffer
            assert buffer.text == "$"
            assert buffer.complete_state is not None
            assert buffer.complete_state.complete_index == 0
            assert buffer.complete_state.current_completion.text == "$alpha "
        finally:
            await runtime.close()


@pytest.mark.anyio
async def test_skill_menu_keeps_all_matches_beyond_visible_height() -> None:
    with create_pipe_input() as pipe_input:
        runtime = TuiRuntime(input_obj=pipe_input, output_obj=DummyOutput())
        runtime.input_model.set_skills(tuple(
            skill_spec(f"skill-{index:02d}")
            for index in range(1, 13)
        ))

        runtime.screen.application.output.get_size = lambda: Size(
            rows=10,
            columns=100,
        )

        await runtime.open()
        try:
            pipe_input.send_text("$")
            await wait_for_completion(runtime)

            buffer = runtime.screen.input.buffer
            assert buffer.complete_state is not None
            assert len(buffer.complete_state.completions) == 12
            assert runtime.screen._completion_height() == 7
            assert runtime.screen.completion_menu.content.right_margins

            runtime.input_model._select_completion(buffer, 11)

            assert buffer.complete_state.current_completion.text == "$skill-12 "
        finally:
            await runtime.close()


@pytest.mark.anyio
async def test_backspacing_skill_query_does_not_move_input() -> None:
    with create_pipe_input() as pipe_input:
        runtime = TuiRuntime(input_obj=pipe_input, output_obj=DummyOutput())
        runtime.input_model.set_skills(tuple(
            skill_spec(f"skill-{index:02d}")
            for index in range(1, 13)
        ))

        with patch.object(
            runtime.screen.application.output,
            "get_size",
            return_value=Size(rows=12, columns=40),
        ):
            await runtime.open()
            try:
                pipe_input.send_text("$")
                await wait_for_completion(runtime)
                screen = await render_next_frame(runtime)
                opened = screen.visible_windows_to_write_positions[
                    runtime.screen.input.window
                ].ypos

                query = "$skill-01"
                pipe_input.send_text(query[1:])
                await wait_for_input_text(runtime, query)

                for remaining_length in range(len(query) - 1, -1, -1):
                    pipe_input.send_text("\x7f")
                    expected = query[:remaining_length]
                    await wait_for_input_text(runtime, expected)
                    screen = await render_next_frame(runtime)
                    positions = screen.visible_windows_to_write_positions

                    assert (
                        positions[runtime.screen.input.window].ypos
                        == opened
                    )
                    assert runtime.screen.canvas_spacer not in positions
            finally:
                await runtime.close()


@pytest.mark.anyio
@pytest.mark.parametrize(
    "command",
    (
        "/helix-link",
        "/helix-stop",
        "/fork",
        "/compact",
        "/mcp stop",
        "/mcp status",
    ),
)
@pytest.mark.parametrize("prior_line_count", (0, 20))
async def test_slash_command_result_releases_completion_layout(
    command: str,
    prior_line_count: int,
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
                if prior_line_count:
                    runtime.append_block(
                        FragmentBlock(((
                            "",
                            "\n".join(
                                f"prior line {index}"
                                for index in range(prior_line_count)
                            ),
                        ),)),
                        kind="assistant",
                    )
                    await render_next_frame(runtime)

                read_task = asyncio.create_task(runtime.read_message(
                    PromptContext(model="test"),
                ))

                pipe_input.send_text("/")
                await wait_for_completion(runtime)
                await render_next_frame(runtime)

                pipe_input.send_text(command[1:])
                await wait_for_input_text(runtime, command)
                screen = await render_next_frame(runtime)
                command_input = screen.visible_windows_to_write_positions[
                    runtime.screen.input.window
                ]
                command_input_row = (
                    12
                    - runtime.screen._visible_height()
                    + command_input.ypos
                )

                pipe_input.send_text("\r")
                assert await read_task == command

                runtime.begin_command_layout()
                render_revision = runtime.screen.application.render_counter
                runtime.append_block(
                    FragmentBlock((("", "■ command completed"),)),
                    kind="operation",
                )
                runtime.discard_pending_submission()
                runtime.finish_command_layout()

                screen = await render_next_frame(runtime)
                positions = screen.visible_windows_to_write_positions
                input_position = positions[runtime.screen.input.window]
                rows = {
                    row: "".join(
                        cells[column].char for column in sorted(cells)
                    ).rstrip()
                    for row, cells in screen.data_buffer.items()
                }
                result_row = next(
                    row
                    for row, text in rows.items()
                    if "command completed" in text
                )

                assert input_position.ypos - result_row == 3
                final_input_row = (
                    12
                    - runtime.screen._visible_height()
                    + input_position.ypos
                )
                assert final_input_row > command_input_row
                assert all(
                    not rows.get(row)
                    for row in range(result_row + 1, input_position.ypos)
                )
                assert runtime.screen._bottom_release_height() == 0
                assert runtime.screen.canvas_spacer not in positions
                assert not runtime.command_layout_pending

                for _ in range(5):
                    await asyncio.sleep(0)
                assert runtime.screen.application.render_counter == (
                    render_revision + 1
                )
            finally:
                await runtime.close()


@pytest.mark.anyio
async def test_stream_command_result_has_no_bottom_release_after_turn() -> None:
    with create_pipe_input() as pipe_input:
        runtime = TuiRuntime(input_obj=pipe_input, output_obj=DummyOutput())

        with patch.object(
            runtime.screen.application.output,
            "get_size",
            return_value=Size(rows=12, columns=40),
        ):
            await runtime.open()
            try:
                handled: list[str] = []

                def handle_stream_command(value: str) -> bool:
                    handled.append(value)
                    return True

                runtime.bind_stream_command_handler(handle_stream_command)
                runtime.set_execution_active(True)
                runtime.set_active_renderable(
                    FragmentBlock((("", "streaming answer"),)),
                    kind="assistant",
                )

                pipe_input.send_text("/")
                await wait_for_completion(runtime)
                await render_next_frame(runtime)
                pipe_input.send_text("helix-link\r")

                loop = asyncio.get_running_loop()
                deadline = loop.time() + 1.0
                while not handled and loop.time() < deadline:
                    await asyncio.sleep(0.001)
                assert handled == ["/helix-link"]

                runtime.queue_background_block(
                    FragmentBlock((("", "■ Helix MCP ready"),)),
                )
                runtime.commit_active_renderable(
                    FragmentBlock((("", "streaming answer\nfinished"),)),
                )
                runtime.set_execution_active(False)

                screen = await render_next_frame(runtime)
                positions = screen.visible_windows_to_write_positions
                input_position = positions[runtime.screen.input.window]
                rows = {
                    row: "".join(
                        cells[column].char for column in sorted(cells)
                    ).rstrip()
                    for row, cells in screen.data_buffer.items()
                }
                result_row = next(
                    row
                    for row, text in rows.items()
                    if "Helix MCP ready" in text
                )

                assert input_position.ypos - result_row == 3
                assert runtime.screen._bottom_release_height() == 0
            finally:
                runtime.set_execution_active(False)
                await runtime.close()


@pytest.mark.anyio
async def test_complete_skill_remains_selected_until_it_is_accepted() -> None:
    with create_pipe_input() as pipe_input:
        runtime = TuiRuntime(input_obj=pipe_input, output_obj=DummyOutput())
        runtime.input_model.set_skills((skill_spec("alpha"),))

        await runtime.open()
        try:
            pipe_input.send_text("$alpha")
            await wait_for_completion(runtime)

            buffer = runtime.screen.input.buffer
            assert buffer.text == "$alpha"
            assert buffer.complete_state is not None
            assert buffer.complete_state.current_completion.text == "$alpha "

            pipe_input.send_text("\t")
            await wait_for_input_text(runtime, "$alpha ")

            assert buffer.complete_state is None
            assert runtime.submissions.message_queue.empty()
        finally:
            await runtime.close()


@pytest.mark.anyio
async def test_unknown_skill_renders_non_selectable_empty_state() -> None:
    with create_pipe_input() as pipe_input:
        runtime = TuiRuntime(input_obj=pipe_input, output_obj=DummyOutput())
        runtime.input_model.set_skills((skill_spec("alpha"),))

        await runtime.open()
        try:
            pipe_input.send_text("$zzz")
            await wait_for_input_text(runtime, "$zzz")
            runtime.screen.application.invalidate()
            await asyncio.sleep(0)

            assert runtime.screen.input.buffer.complete_state is None
            assert runtime.screen._completion_fallback_fragments() == [
                ("class:completion-menu.empty", " no matches"),
            ]
        finally:
            await runtime.close()


@pytest.mark.anyio
async def test_backspace_preserves_selected_skill_without_async_restart() -> None:
    with create_pipe_input() as pipe_input:
        runtime = TuiRuntime(input_obj=pipe_input, output_obj=DummyOutput())
        runtime.input_model.set_skills((
            skill_spec("alpha"),
            skill_spec("alphabet"),
        ))

        await runtime.open()
        try:
            pipe_input.send_text("$alph")
            await wait_for_completion(runtime)

            buffer = runtime.screen.input.buffer
            runtime.input_model._select_completion(buffer, 1)
            assert buffer.complete_state is not None
            assert buffer.complete_state.current_completion.text == "$alphabet "

            with patch.object(
                buffer,
                "start_completion",
                wraps=buffer.start_completion,
            ) as start_completion:
                pipe_input.send_text("\x7f\x7f")
                await wait_for_input_text(runtime, "$al")

            start_completion.assert_not_called()
            assert buffer.complete_state is not None
            assert buffer.complete_state.current_completion.text == "$alphabet "
        finally:
            await runtime.close()


def test_dismissed_slash_menu_reopens_after_editing() -> None:
    runtime = TuiRuntime()
    buffer = runtime.screen.input.buffer
    buffer.document = Document("/aaa", cursor_position=4)

    runtime.input_model.dismiss_completion_menu(buffer)
    assert not runtime.screen._completion_visible()

    buffer.document = Document("/aaax", cursor_position=5)
    buffer.document = Document("/aaa", cursor_position=4)

    assert runtime.screen._completion_fallback_visible()


@pytest.mark.anyio
async def test_backspace_refreshes_inline_suggestion() -> None:
    with create_pipe_input() as pipe_input:
        runtime = TuiRuntime(input_obj=pipe_input, output_obj=DummyOutput())

        await runtime.open()
        try:
            pipe_input.send_text("hiX")
            await wait_for_input_text(runtime, "hiX")

            buffer = runtime.screen.input.buffer
            assert buffer.suggestion is None

            pipe_input.send_text("\x7f")
            await wait_for_input_text(runtime, "hi")
            await wait_for_suggestion(runtime)

            assert buffer.text == "hi"
            assert buffer.suggestion is not None
            assert buffer.suggestion.text == "，请介绍一下你自己"
        finally:
            await runtime.close()


@pytest.mark.anyio
async def test_backspace_reopens_completion_menu() -> None:
    with create_pipe_input() as pipe_input:
        runtime = TuiRuntime(input_obj=pipe_input, output_obj=DummyOutput())

        await runtime.open()
        try:
            pipe_input.send_text("/sX")
            await wait_for_input_text(runtime, "/sX")

            buffer = runtime.screen.input.buffer
            assert buffer.complete_state is None

            pipe_input.send_text("\x7f")
            await wait_for_input_text(runtime, "/s")
            await wait_for_completion(runtime)

            assert buffer.complete_state is not None
            assert [
                completion.display_text
                for completion in buffer.complete_state.completions
            ] == ["/skills", "/shutdown"]
        finally:
            await runtime.close()
