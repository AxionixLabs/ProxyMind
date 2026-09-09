# -*- coding: utf-8 -*-

import dataclasses

import pytest
from prompt_toolkit.data_structures import Point
from prompt_toolkit.data_structures import Size
from prompt_toolkit.layout.screen import Char
from prompt_toolkit.layout.screen import Screen
from prompt_toolkit.output import ColorDepth
from prompt_toolkit.output import DummyOutput
from prompt_toolkit.styles import Attrs
from prompt_toolkit.styles import DEFAULT_ATTRS

from frontends.terminal.capabilities import TerminalCapabilityState
from frontends.terminal.capabilities import TerminalOutputCapabilities
from frontends.tui.rendering.screen.inline_viewport import (
    TuiInlineViewportRenderer,
)


@dataclasses.dataclass(frozen=True, slots=True)
class _OutputEvent:
    """记录一次绝对 viewport 输出动作。"""

    operation: str
    value: str = ""


class _RecordingOutput(DummyOutput):
    """提供不依赖真实终端的输出动作记录。"""

    def __init__(self, *, rows: int = 20, columns: int = 80) -> None:
        self.size = Size(rows=rows, columns=columns)
        self.events: list[_OutputEvent] = []

    def get_size(self) -> Size:
        return self.size

    def write(self, data: str) -> None:
        self.events.append(_OutputEvent("write", data))

    def write_raw(self, data: str) -> None:
        self.events.append(_OutputEvent("write_raw", data))

    def flush(self) -> None:
        self.events.append(_OutputEvent("flush"))

    def hide_cursor(self) -> None:
        self.events.append(_OutputEvent("hide_cursor"))

    def show_cursor(self) -> None:
        self.events.append(_OutputEvent("show_cursor"))

    def disable_autowrap(self) -> None:
        self.events.append(_OutputEvent("disable_autowrap"))

    def enable_autowrap(self) -> None:
        self.events.append(_OutputEvent("enable_autowrap"))

    def reset_attributes(self) -> None:
        self.events.append(_OutputEvent("reset_attributes"))

    def set_attributes(self, _attrs: Attrs, color_depth: ColorDepth) -> None:
        self.events.append(_OutputEvent("set_attributes", color_depth.name))

    def erase_end_of_line(self) -> None:
        self.events.append(_OutputEvent("erase_end_of_line"))

    def cursor_goto(self, row: int = 0, column: int = 0) -> None:
        self.events.append(_OutputEvent("cursor_goto", f"{row},{column}"))


def _capabilities(
    *,
    synchronized_output: TerminalCapabilityState = (
        TerminalCapabilityState.SUPPORTED
    ),
) -> TerminalOutputCapabilities:
    """构造测试专用的显式输出能力快照。"""

    return TerminalOutputCapabilities(
        absolute_cursor_addressing=TerminalCapabilityState.SUPPORTED,
        synchronized_output=synchronized_output,
        viewport_size=TerminalCapabilityState.SUPPORTED,
        startup_cursor_position=TerminalCapabilityState.SUPPORTED,
    )


def _screen(lines: tuple[str, ...]) -> Screen:
    """构造带单宽字符的已完成 prompt_toolkit raster。"""

    width = max((len(line) for line in lines), default=1)
    screen = Screen(initial_width=width, initial_height=max(len(lines), 1))
    for row, line in enumerate(lines):
        for column, value in enumerate(line):
            screen.data_buffer[row][column] = Char(value)
    return screen


def _wide_screen() -> Screen:
    """构造包含双宽与组合字符的 raster。"""

    screen = Screen(initial_width=5, initial_height=1)
    screen.data_buffer[0][0] = Char("界")
    screen.data_buffer[0][2] = Char("e\u0301")
    screen.data_buffer[0][3] = Char("A")
    return screen


def _renderer(
    output: _RecordingOutput,
    *,
    synchronized_output: TerminalCapabilityState = (
        TerminalCapabilityState.SUPPORTED
    ),
    scroll_lines=None,
) -> TuiInlineViewportRenderer:
    return TuiInlineViewportRenderer(
        output,
        capabilities=_capabilities(
            synchronized_output=synchronized_output,
        ),
        viewport_origin=Point(x=4, y=2),
        scroll_lines=scroll_lines or (lambda _amount: None),
        style_resolver=lambda _style: DEFAULT_ATTRS,
    )


def _writes(output: _RecordingOutput) -> list[str]:
    return [
        event.value
        for event in output.events
        if event.operation == "write"
    ]


def _cursor_gotos(output: _RecordingOutput) -> list[str]:
    return [
        event.value
        for event in output.events
        if event.operation == "cursor_goto"
    ]


def test_renderer_uses_absolute_coordinates_without_vertical_crlf() -> None:
    output = _RecordingOutput(rows=12, columns=40)
    renderer = _renderer(output)

    renderer.render(
        _screen(("first", "second")),
        cursor_position=Point(x=2, y=1),
        terminal_size=output.size,
        size_generation=0,
    )

    assert all("\r\n" not in value for value in _writes(output))
    assert "3,4" in _cursor_gotos(output)
    assert renderer.viewport_origin == Point(x=4, y=2)
    assert renderer.viewport_size == Size(rows=2, columns=6)
    assert renderer.final_cursor == Point(x=6, y=3)
    assert renderer.terminal_size_generation == 0


def test_renderer_repaints_only_changed_cell_after_external_visual_insertion() -> None:
    output = _RecordingOutput(rows=12, columns=40)
    renderer = _renderer(output)
    first = _screen(("first",))
    renderer.render(
        first,
        cursor_position=Point(x=5, y=0),
        terminal_size=output.size,
        size_generation=0,
    )

    output.events.clear()
    changed = _screen(("fIrst",))
    renderer.render(
        changed,
        cursor_position=Point(x=5, y=0),
        terminal_size=output.size,
        size_generation=0,
    )

    assert _writes(output) == ["I"]
    assert _cursor_gotos(output) == ["2,5", "2,8"]
    assert all(
        event.operation not in {"cursor_up", "cursor_down"}
        for event in output.events
    )


def test_identical_frame_has_no_content_write() -> None:
    output = _RecordingOutput(rows=12, columns=40)
    renderer = _renderer(output)
    screen = _screen(("stable",))
    renderer.render(
        screen,
        cursor_position=Point(x=1, y=0),
        terminal_size=output.size,
        size_generation=0,
    )

    output.events.clear()
    renderer.render(
        screen,
        cursor_position=Point(x=1, y=0),
        terminal_size=output.size,
        size_generation=0,
    )

    assert _writes(output) == []
    assert _cursor_gotos(output) == ["2,5"]


def test_viewport_growth_scrolls_exact_required_rows_and_refixes_origin() -> None:
    output = _RecordingOutput(rows=6, columns=40)
    scrolled: list[int] = []

    def scroll(amount: int) -> None:
        scrolled.append(amount)
        output.events.append(_OutputEvent("scroll", str(amount)))

    renderer = _renderer(output, scroll_lines=scroll)

    renderer.render(
        _screen(("one", "two")),
        cursor_position=Point(x=0, y=0),
        terminal_size=output.size,
        size_generation=0,
    )
    output.events.clear()
    renderer.render(
        _screen(("one", "two", "three", "four", "five")),
        cursor_position=Point(x=0, y=4),
        terminal_size=output.size,
        size_generation=0,
    )

    assert scrolled == [1]
    assert renderer.viewport_origin == Point(x=4, y=1)
    assert renderer.viewport_size.rows == 5
    assert renderer.final_cursor == Point(x=4, y=5)
    begin = next(
        index
        for index, event in enumerate(output.events)
        if event.value == "\x1b[?2026h"
    )
    scroll_index = next(
        index
        for index, event in enumerate(output.events)
        if event.operation == "scroll"
    )
    end = next(
        index
        for index, event in enumerate(output.events)
        if event.value == "\x1b[?2026l"
    )
    assert begin < scroll_index < end


def test_shrinking_frame_clears_wide_trailing_cells_and_keeps_combining_text() -> None:
    output = _RecordingOutput(rows=12, columns=40)
    renderer = _renderer(output)
    renderer.render(
        _wide_screen(),
        cursor_position=Point(x=3, y=0),
        terminal_size=output.size,
        size_generation=0,
    )
    initial_writes = _writes(output)
    assert "界" in initial_writes
    assert "e\u0301" in initial_writes

    output.events.clear()
    narrowed = _screen(("界",))
    renderer.render(
        narrowed,
        cursor_position=Point(x=2, y=0),
        terminal_size=output.size,
        size_generation=0,
    )

    assert "e\u0301" not in _writes(output)
    assert "界" not in _writes(output)
    assert "2,6" in _cursor_gotos(output)
    assert [
        event.operation
        for event in output.events
        if event.operation == "erase_end_of_line"
    ] == ["erase_end_of_line"]


def test_size_generation_change_clears_old_viewport_before_full_repaint() -> None:
    output = _RecordingOutput(rows=12, columns=40)
    renderer = _renderer(output)
    screen = _screen(("old", "frame"))
    renderer.render(
        screen,
        cursor_position=Point(x=0, y=0),
        terminal_size=output.size,
        size_generation=0,
    )

    output.events.clear()
    renderer.render(
        _screen(("new",)),
        cursor_position=Point(x=0, y=0),
        terminal_size=output.size,
        size_generation=1,
    )

    operations = [event.operation for event in output.events]
    assert operations.index("erase_end_of_line") < operations.index("write")
    assert _writes(output)[:3] == ["n", "e", "w"]


def test_terminal_resize_clears_previous_viewport_before_repainting() -> None:
    output = _RecordingOutput(rows=12, columns=40)
    renderer = _renderer(output)
    screen = _screen(("stable",))
    renderer.render(
        screen,
        cursor_position=Point(x=0, y=0),
        terminal_size=output.size,
        size_generation=0,
    )

    output.events.clear()
    resized = Size(rows=10, columns=30)
    renderer.render(
        screen,
        cursor_position=Point(x=0, y=0),
        terminal_size=resized,
        size_generation=0,
    )

    operations = [event.operation for event in output.events]
    assert operations.index("erase_end_of_line") < operations.index("write")
    assert renderer.viewport_size == Size(rows=1, columns=6)
    assert renderer.terminal_size_generation == 0


def test_synchronized_transaction_orders_boundaries_content_and_final_cursor() -> None:
    output = _RecordingOutput(rows=12, columns=40)
    renderer = _renderer(output)

    renderer.render(
        _screen(("sync",)),
        cursor_position=Point(x=1, y=0),
        terminal_size=output.size,
        size_generation=0,
    )

    begin = next(
        index
        for index, event in enumerate(output.events)
        if event.value == "\x1b[?2026h"
    )
    end = next(
        index
        for index, event in enumerate(output.events)
        if event.value == "\x1b[?2026l"
    )
    content = next(
        index
        for index, event in enumerate(output.events)
        if event.operation == "write" and event.value == "s"
    )
    final_cursor = max(
        index
        for index, event in enumerate(output.events)
        if event.operation == "cursor_goto"
    )

    assert begin < content < final_cursor < end
    assert output.events[begin + 1].operation == "flush"
    assert output.events[end + 1].operation == "flush"


def test_renderer_preserves_style_and_zero_width_terminal_metadata() -> None:
    output = _RecordingOutput(rows=12, columns=40)
    screen = _screen(("x",))
    screen.data_buffer[0][0] = Char("x", "link")
    screen.zero_width_escapes[0][0] = "\x1b]8;;https://example.test\x1b\\"
    attrs = Attrs(
        color="ansiblue",
        bgcolor="ansiblack",
        bold=True,
        underline=False,
        strike=False,
        italic=False,
        blink=False,
        reverse=False,
        hidden=False,
        dim=False,
    )
    renderer = TuiInlineViewportRenderer(
        output,
        capabilities=_capabilities(),
        viewport_origin=Point(x=4, y=2),
        scroll_lines=lambda _amount: None,
        style_resolver=lambda style: attrs if style == "link" else Attrs(
            color="",
            bgcolor="",
            bold=False,
            underline=False,
            strike=False,
            italic=False,
            blink=False,
            reverse=False,
            hidden=False,
            dim=False,
        ),
    )

    renderer.render(
        screen,
        cursor_position=Point(x=0, y=0),
        terminal_size=output.size,
        size_generation=0,
    )

    assert "x" in _writes(output)
    assert "\x1b]8;;https://example.test\x1b\\" in [
        event.value
        for event in output.events
        if event.operation == "write_raw"
    ]
    assert any(
        event.operation == "set_attributes" and event.value == "DEPTH_8_BIT"
        for event in output.events
    )
    assert [
        event.operation
        for event in output.events
        if event.operation in {"disable_autowrap", "enable_autowrap"}
    ] == ["disable_autowrap", "enable_autowrap"]


def test_unsupported_synchronized_output_keeps_absolute_coordinate_semantics() -> None:
    output = _RecordingOutput(rows=12, columns=40)
    renderer = _renderer(
        output,
        synchronized_output=TerminalCapabilityState.UNSUPPORTED,
    )

    renderer.render(
        _screen(("plain",)),
        cursor_position=Point(x=0, y=0),
        terminal_size=output.size,
        size_generation=0,
    )

    assert not [
        event
        for event in output.events
        if event.operation == "write_raw"
        and event.value in {"\x1b[?2026h", "\x1b[?2026l"}
    ]
    assert "2,4" in _cursor_gotos(output)


def test_renderer_rejects_missing_absolute_cursor_capability() -> None:
    capabilities = dataclasses.replace(
        _capabilities(),
        absolute_cursor_addressing=TerminalCapabilityState.UNKNOWN,
    )

    with pytest.raises(ValueError, match="absolute cursor addressing"):
        TuiInlineViewportRenderer(
            _RecordingOutput(),
            capabilities=capabilities,
            viewport_origin=Point(x=0, y=0),
            scroll_lines=lambda _amount: None,
            style_resolver=lambda _style: DEFAULT_ATTRS,
        )


if __name__ == '__main__':
    pass
