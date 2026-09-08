# -*- coding: utf-8 -*-

"""验证 slash command 的选择、补全与提交行为。"""


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


async def wait_for_no_completion(runtime: TuiRuntime) -> None:
    """等待当前输入的补全菜单收起。"""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + 1.0
    while loop.time() < deadline:
        if runtime.screen.input.buffer.complete_state is None:
            return
        await asyncio.sleep(0.001)
    raise AssertionError("completion did not close")


async def wait_for_input_text(runtime: TuiRuntime, text: str) -> None:
    """等待管道输入被主输入框完整消费。"""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + 1.0
    while loop.time() < deadline:
        if runtime.screen.input.buffer.text == text:
            return
        await asyncio.sleep(0.001)
    raise AssertionError(f"input text did not become {text!r}")


async def wait_for_cursor_position(runtime: TuiRuntime, position: int) -> None:
    """等待输入光标移动到指定位置。"""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + 1.0
    while loop.time() < deadline:
        if runtime.screen.input.buffer.cursor_position == position:
            return
        await asyncio.sleep(0.001)
    raise AssertionError(f"input cursor did not move to {position}")


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


def rendered_window_line(runtime: TuiRuntime, window) -> str:
    """返回指定窗口最近一次渲染的首行文本。"""
    screen = runtime.screen.application.renderer.last_rendered_screen
    position = screen.visible_windows_to_write_positions[window]
    row = screen.data_buffer[position.ypos]
    return "".join(
        row[column].char
        for column in range(position.xpos + position.width)
    ).rstrip()


@pytest.mark.anyio
async def test_input_placeholder_reserves_first_cell_for_cursor() -> None:
    with create_pipe_input() as pipe_input:
        runtime = TuiRuntime(input_obj=pipe_input, output_obj=DummyOutput())
        runtime.submissions.placeholder_text = "Write tests for @filename"

        await runtime.open()
        try:
            runtime.screen.application.invalidate()
            await asyncio.sleep(0)

            screen = runtime.screen.application.renderer.last_rendered_screen
            input_position = screen.visible_windows_to_write_positions[
                runtime.screen.input.window
            ]
            cursor = screen.get_cursor_position(runtime.screen.input.window)

            assert cursor.x == input_position.xpos
            assert rendered_input_line(runtime) == "›  Write tests for @filename"
        finally:
            await runtime.close()


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("shell_mode", "prompt"),
    (
        (False, "›"),
        (True, "!"),
    ),
)
async def test_input_prompt_is_separate_from_multiline_and_wrapped_text(
    shell_mode: bool,
    prompt: str,
) -> None:
    with create_pipe_input() as pipe_input:
        runtime = TuiRuntime(input_obj=pipe_input, output_obj=DummyOutput())

        with patch.object(
            runtime.screen.application.output,
            "get_size",
            return_value=Size(rows=12, columns=20),
        ):
            await runtime.open()
            try:
                runtime.input_model.set_shell_mode(shell_mode)
                runtime.screen.input.buffer.document = Document(
                    "abcdefghijklmnopqrs\nsecond",
                    cursor_position=len("abcdefghijklmnopqrs\nsecond"),
                )

                screen = await render_next_frame(runtime)
                positions = screen.visible_windows_to_write_positions
                prompt_position = positions[
                    runtime.screen.input_prompt_window
                ]
                input_position = positions[runtime.screen.input.window]
                cursor = screen.get_cursor_position(runtime.screen.input.window)

                assert prompt_position.width == 2
                assert input_position.xpos == prompt_position.xpos + 2
                assert input_position.width == 17
                assert runtime.screen.input.window.get_line_prefix is None
                assert rendered_input_lines(runtime) == [
                    f"{prompt} abcdefghijklmnopq",
                    "  rs",
                    "  second",
                ]
                assert cursor.x - input_position.xpos == len("second")
                assert cursor.y - input_position.ypos == 2
            finally:
                await runtime.close()


@pytest.mark.anyio
async def test_input_prompt_stays_single_when_textarea_scrolls() -> None:
    with create_pipe_input() as pipe_input:
        runtime = TuiRuntime(input_obj=pipe_input, output_obj=DummyOutput())

        with patch.object(
            runtime.screen.application.output,
            "get_size",
            return_value=Size(rows=8, columns=20),
        ):
            await runtime.open()
            try:
                text = "\n".join(f"line {index}" for index in range(12))
                runtime.screen.input.buffer.document = Document(
                    text,
                    cursor_position=len(text),
                )

                await render_next_frame(runtime)
                lines = rendered_input_lines(runtime)

                assert len(lines) < len(text.splitlines())
                assert lines[0].startswith("› ")
                assert lines[-1] == "  line 11"
                assert all(line.startswith("  ") for line in lines[1:])
                assert sum(line.count("›") for line in lines) == 1
                assert not any(line.startswith((". ", "! ")) for line in lines)
            finally:
                await runtime.close()


@pytest.mark.anyio
async def test_model_command_does_not_show_inline_hint() -> None:
    with create_pipe_input() as pipe_input:
        runtime = TuiRuntime(input_obj=pipe_input, output_obj=DummyOutput())

        await runtime.open()
        try:
            pipe_input.send_text("/model ")
            await wait_for_input_text(runtime, "/model ")
            runtime.screen.application.invalidate()
            await asyncio.sleep(0)

            assert runtime.screen.input.buffer.suggestion is None
            assert rendered_input_line(runtime) == "› /model"
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
            menu_window = runtime.screen.completion_menu
            completion_line = rendered_window_line(runtime, menu_window)
            screen = runtime.screen.application.renderer.last_rendered_screen
            bottom_position = screen.visible_windows_to_write_positions[
                runtime.screen.input_bottom_padding
            ]
            menu_position = screen.visible_windows_to_write_positions[menu_window]

            assert input_line == "› /sk"
            assert completion_line.lstrip().startswith("/skills")
            assert input_line.index("/") == completion_line.index("/")
            assert menu_position.xpos == 0
            assert "class:token-menu.command.current" in (
                screen.data_buffer[menu_position.ypos][
                    menu_position.xpos + 2
                ].style
            )
            meta_column = input_line.index("/") + len("/skills") + 2
            assert "class:token-menu.meta.command.current" in (
                screen.data_buffer[menu_position.ypos][meta_column].style
            )
            assert menu_position.ypos == bottom_position.ypos + 1
        finally:
            await runtime.close()


@pytest.mark.anyio
@pytest.mark.parametrize("command", ["/new", "/mcp"])
async def test_exact_slash_completion_uses_same_menu_as_prefix_command(
    command: str,
) -> None:
    with create_pipe_input() as pipe_input:
        runtime = TuiRuntime(input_obj=pipe_input, output_obj=DummyOutput())

        await runtime.open()
        try:
            pipe_input.send_text(command)
            await wait_for_input_text(runtime, command)
            runtime.screen.application.invalidate()
            await asyncio.sleep(0)

            input_line = rendered_input_line(runtime)
            menu_window = runtime.screen.completion_menu
            completion_line = rendered_window_line(runtime, menu_window)
            screen = runtime.screen.application.renderer.last_rendered_screen
            menu_position = screen.visible_windows_to_write_positions[
                menu_window
            ]

            assert input_line == f"› {command}"
            assert completion_line.lstrip().startswith(command)
            assert input_line.index("/") == completion_line.index("/")
            assert menu_position.xpos == 0
            assert "class:token-menu.command.current" in (
                screen.data_buffer[menu_position.ypos][
                    menu_position.xpos + 2
                ].style
            )
            meta_column = input_line.index("/") + len(command) + 2
            assert "class:token-menu.meta.command.current" in (
                screen.data_buffer[menu_position.ypos][meta_column].style
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
            assert runtime.screen._native_completion_visible()
            snapshot = runtime.input_model.token_menu_snapshot(
                runtime.screen.input.buffer,
            )
            assert snapshot is not None
            assert snapshot.items[0].display_text == "/fork"
        finally:
            await runtime.close()


@pytest.mark.anyio
async def test_tab_dispatches_selected_skills_command() -> None:
    with create_pipe_input() as pipe_input:
        runtime = TuiRuntime(input_obj=pipe_input, output_obj=DummyOutput())

        await runtime.open()
        try:
            pipe_input.send_text("/sk")
            await wait_for_completion(runtime)
            pipe_input.send_text("\t")

            submission = await asyncio.wait_for(
                runtime.submissions.message_queue.get(),
                timeout=1,
            )
            assert submission.value == "/skills"
        finally:
            await runtime.close()


@pytest.mark.anyio
async def test_slash_key_completes_selected_slash_command_without_submitting() -> None:
    with create_pipe_input() as pipe_input:
        runtime = TuiRuntime(input_obj=pipe_input, output_obj=DummyOutput())

        await runtime.open()
        try:
            pipe_input.send_text("/m")
            await wait_for_completion(runtime)
            pipe_input.send_text("/")
            await wait_for_input_text(runtime, "/model ")

            assert runtime.submissions.message_queue.empty()
            assert runtime.screen.input.buffer.suggestion is None
        finally:
            await runtime.close()


@pytest.mark.anyio
async def test_slash_menu_survives_left_and_right_cursor_motion() -> None:
    with create_pipe_input() as pipe_input:
        runtime = TuiRuntime(input_obj=pipe_input, output_obj=DummyOutput())

        await runtime.open()
        try:
            pipe_input.send_text("/sx")
            await wait_for_input_text(runtime, "/sx")

            pipe_input.send_text("\x1b[D")
            await wait_for_cursor_position(runtime, 2)
            await wait_for_completion(runtime)

            buffer = runtime.screen.input.buffer
            assert buffer.complete_state is not None
            assert [
                completion.display_text
                for completion in buffer.complete_state.completions
            ] == ["/stop", "/skills", "/shutdown"]

            pipe_input.send_text("\x1b[C")
            await wait_for_cursor_position(runtime, 3)
            await wait_for_no_completion(runtime)

            assert buffer.complete_state is None

            pipe_input.send_text("\x1b[D")
            await wait_for_cursor_position(runtime, 2)
            await wait_for_completion(runtime)

            assert buffer.complete_state is not None
            assert [
                completion.display_text
                for completion in buffer.complete_state.completions
            ] == ["/stop", "/skills", "/shutdown"]
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
            assert runtime.screen.input.buffer.suggestion is None
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
                ("class:completion-menu.empty", "  no matches"),
            ]
            assert rendered_window_line(
                runtime,
                runtime.screen.completion_fallback_window,
            ).lstrip() == "no matches"
        finally:
            await runtime.close()
