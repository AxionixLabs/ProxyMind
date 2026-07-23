# -*- coding: utf-8 -*-

import asyncio
from unittest.mock import patch

import pytest
from prompt_toolkit.data_structures import Size
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
                assert rendered_input_line(runtime) == "> /"
            finally:
                await runtime.close()
