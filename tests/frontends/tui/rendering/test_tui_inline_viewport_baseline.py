# -*- coding: utf-8 -*-

"""冻结 inline viewport 改造前的终端输出与逻辑布局事实。"""


import asyncio
import dataclasses

import pytest
from prompt_toolkit.data_structures import Size
from prompt_toolkit.input.defaults import create_pipe_input
from prompt_toolkit.output import DummyOutput

from frontends.tui.core.runtime import TuiRuntime
from tests.frontends.tui.rendering.frame_scenarios import block as _block


@dataclasses.dataclass(frozen=True, slots=True)
class _OutputEvent:
    """保存一次 prompt_toolkit 输出调用及其参数。"""

    operation: str
    value: str = ""


class _RecordingInlineOutput(DummyOutput):
    """记录旧 inline renderer 的相对移动和帧边界。"""

    def __init__(self, *, columns: int, rows: int) -> None:
        self._size = Size(rows=rows, columns=columns)
        self.events: list[_OutputEvent] = []

    def get_size(self) -> Size:
        """返回固定的测试终端尺寸。"""

        return self._size

    def get_rows_below_cursor_position(self) -> int:
        """返回固定的 inline 可用行数。"""

        return self._size.rows

    def write(self, data: str) -> None:
        """记录普通输出，包括 renderer 用于下移的换行。"""

        self.events.append(_OutputEvent("write", data))

    def write_raw(self, data: str) -> None:
        """记录原始终端控制序列。"""

        self.events.append(_OutputEvent("write_raw", data))

    def flush(self) -> None:
        """记录帧 flush。"""

        self.events.append(_OutputEvent("flush"))

    def erase_screen(self) -> None:
        """记录清屏。"""

        self.events.append(_OutputEvent("erase_screen"))

    def erase_end_of_line(self) -> None:
        """记录行尾清理。"""

        self.events.append(_OutputEvent("erase_end_of_line"))

    def erase_down(self) -> None:
        """记录向下清理。"""

        self.events.append(_OutputEvent("erase_down"))

    def scroll_buffer_to_prompt(self) -> None:
        """记录 Windows inline prompt 滚动。"""

        self.events.append(_OutputEvent("scroll_buffer_to_prompt"))

    def cursor_goto(self, row: int = 0, column: int = 0) -> None:
        """记录绝对光标定位调用。"""

        self.events.append(_OutputEvent("cursor_goto", f"{row},{column}"))

    def cursor_up(self, amount: int) -> None:
        """记录相对向上移动。"""

        self.events.append(_OutputEvent("cursor_up", str(amount)))

    def cursor_down(self, amount: int) -> None:
        """记录相对向下移动。"""

        self.events.append(_OutputEvent("cursor_down", str(amount)))

    def cursor_forward(self, amount: int) -> None:
        """记录相对向右移动。"""

        self.events.append(_OutputEvent("cursor_forward", str(amount)))

    def cursor_backward(self, amount: int) -> None:
        """记录相对向左移动。"""

        self.events.append(_OutputEvent("cursor_backward", str(amount)))


@dataclasses.dataclass(frozen=True, slots=True)
class _ExternalPreedit:
    """表示由宿主终端绘制、但不属于 TUI Buffer 的输入法预编辑。"""

    text: str
    visual_rows: int = 1


def _footer_position(runtime: TuiRuntime) -> tuple[int, int]:
    """返回当前 raster 中 footer 的逻辑位置和高度。"""

    screen = runtime.screen.application.renderer.last_rendered_screen
    if screen is None:
        raise AssertionError("baseline frame was not rendered")
    position = screen.visible_windows_to_write_positions[runtime.screen.footer_window]
    return position.ypos, position.height


async def _next_frame(runtime: TuiRuntime) -> None:
    """等待一次明确请求的下一帧。"""

    previous_revision = runtime.screen.application.render_counter
    runtime.invalidate()
    for _ in range(20):
        await asyncio.sleep(0)
        if runtime.screen.application.render_counter > previous_revision:
            return None
    raise AssertionError("next frame was not rendered")


@pytest.mark.anyio
async def test_baseline_layout_keeps_footer_logic_position_for_external_preedit() -> None:
    """证明宿主预编辑不进入 Buffer，也不改变 Mind 的逻辑 footer 布局。"""

    with create_pipe_input() as pipe_input:
        output = _RecordingInlineOutput(columns=40, rows=12)
        runtime = TuiRuntime(input_obj=pipe_input, output_obj=output)
        await runtime.open()
        try:
            runtime.screen.input.buffer.text = "draft"
            await _next_frame(runtime)
            before = _footer_position(runtime)
            before_height = runtime.screen._input_content_height(width=40)

            preedit = _ExternalPreedit(text="ni hao", visual_rows=1)

            assert preedit.text not in runtime.screen.input.buffer.text
            assert preedit.visual_rows == 1
            assert runtime.screen.input.buffer.text == "draft"
            assert runtime.screen._input_content_height(width=40) == before_height
            assert _footer_position(runtime) == before
        finally:
            await runtime.close()


@pytest.mark.anyio
async def test_baseline_inline_renderer_uses_relative_newline_for_vertical_growth() -> None:
    """冻结 prompt_toolkit 旧 renderer 通过相对换行保留垂直空间的事实。"""

    with create_pipe_input() as pipe_input:
        output = _RecordingInlineOutput(columns=40, rows=12)
        runtime = TuiRuntime(input_obj=pipe_input, output_obj=output)
        await runtime.open()
        try:
            output.events.clear()
            runtime.screen.set_activity_renderable(_block("Thinking"))
            await _next_frame(runtime)

            newline_writes = [
                event.value
                for event in output.events
                if event.operation == "write" and "\r\n" in event.value
            ]

            assert newline_writes, (
                "inline baseline did not record prompt_toolkit relative CRLF output"
            )
        finally:
            await runtime.close()


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("columns", "rows", "text", "expected_footer_y"),
    (
        (40, 12, "", 4),
        (40, 12, "中文", 4),
        (40, 12, "first\nsecond", 5),
        (20, 8, "边界", 4),
    ),
)
async def test_baseline_raster_records_footer_for_input_width_cases(
    columns: int,
    rows: int,
    text: str,
    expected_footer_y: int,
) -> None:
    """冻结不同输入宽度和文本形态下的 footer 逻辑位置。"""

    with create_pipe_input() as pipe_input:
        output = _RecordingInlineOutput(columns=columns, rows=rows)
        runtime = TuiRuntime(input_obj=pipe_input, output_obj=output)
        await runtime.open()
        try:
            runtime.screen.input.buffer.text = text
            await _next_frame(runtime)

            footer_y, footer_height = _footer_position(runtime)
            assert footer_height == 1
            assert footer_y == expected_footer_y
            rendered_screen = runtime.screen.application.renderer.last_rendered_screen
            if rendered_screen is None:
                raise AssertionError("baseline frame was not rendered")
            assert footer_y + footer_height <= rendered_screen.height
        finally:
            await runtime.close()
