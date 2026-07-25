# -*- coding: utf-8 -*-

import asyncio
from unittest.mock import patch

import pytest
from prompt_toolkit.completion import Completion
from prompt_toolkit.data_structures import Size
from prompt_toolkit.document import Document
from prompt_toolkit.input.defaults import create_pipe_input
from prompt_toolkit.output import DummyOutput

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
    assert not runtime.screen._footer_visible()

    buffer.document = Document("/mcp", cursor_position=4)

    assert not runtime.screen._completion_visible()
    assert runtime.screen._footer_visible()


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
                assert buffer.complete_state.complete_index is None
                assert rendered_input_line(runtime) == "› /"
            finally:
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

            assert rendered_input_line(runtime) == "› Write tests for @filename"
        finally:
            await runtime.close()


@pytest.mark.anyio
async def test_slash_completion_aligns_with_input_command() -> None:
    with create_pipe_input() as pipe_input:
        runtime = TuiRuntime(input_obj=pipe_input, output_obj=DummyOutput())

        await runtime.open()
        try:
            pipe_input.send_text("/s")
            await wait_for_completion(runtime)
            runtime.screen.application.invalidate()
            await asyncio.sleep(0)

            input_line = rendered_input_line(runtime)
            menu_window = runtime.screen.completion_menu.content
            completion_line = rendered_window_line(runtime, menu_window)

            assert input_line == "› /s"
            assert completion_line.lstrip().startswith("/skills")
            assert input_line.index("/") == completion_line.index("/")
        finally:
            await runtime.close()


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
