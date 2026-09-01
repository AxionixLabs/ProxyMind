# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
from prompt_toolkit.cursor_shapes import CursorShape
from prompt_toolkit.data_structures import Size
from prompt_toolkit.layout.containers import Window
from prompt_toolkit.layout.screen import (
    Char,
    Screen,
)
from prompt_toolkit.output import (
    ColorDepth,
    Output
)
from prompt_toolkit.styles import Attrs
from frontends.terminal.text import sanitize_terminal_hyperlink
from .models import FormattedText

OSC8_PREFIX = "\x1b]8;;"
OSC8_SUFFIX = "\x1b\\"
OSC8_CLOSE = f"{OSC8_PREFIX}{OSC8_SUFFIX}"


class _ScreenWritePosition(typing.Protocol):
    """描述窗口写入区域所需的坐标和尺寸。"""
    xpos: int
    ypos: int
    width: int
    height: int


class TerminalHyperlinkStyle(str):
    """保存视觉样式及其独立的终端链接目标。"""

    _destination: str

    def __new__(
        cls,
        style: str,
        destination: str,
    ) -> "TerminalHyperlinkStyle":
        value = typing.cast(
            TerminalHyperlinkStyle,
            super().__new__(cls, str(style or "")),
        )
        object.__setattr__(value, "_destination", destination)
        return value

    @property
    def destination(self) -> str:
        """返回不可变的终端链接目标。"""
        return self._destination

    def __setattr__(self, name: str, value: typing.Any) -> None:
        raise AttributeError(
            f"{type(self).__name__} attributes are immutable"
        )

    def __hash__(self) -> int:
        return hash((str(self), self.destination))

    def __eq__(self, other: object) -> bool:
        return bool(
            isinstance(other, TerminalHyperlinkStyle)
            and str.__eq__(self, other)
            and self.destination == other.destination
        )

    def __ne__(self, other: object) -> bool:
        return not self == other

    def __reduce__(
        self,
    ) -> tuple[type["TerminalHyperlinkStyle"], tuple[str, str]]:
        return type(self), (str(self), self.destination)


def terminal_hyperlink_style(style: str, destination: str | None) -> str:
    """把安全链接目标附加为不参与视觉计算的样式元数据。"""
    safe_destination = sanitize_terminal_hyperlink(destination)
    if not safe_destination:
        return style

    return TerminalHyperlinkStyle(style, safe_destination)


def terminal_hyperlink_from_style(style: str) -> str | None:
    """从样式元数据中读取经过校验的链接目标。"""
    if not isinstance(style, TerminalHyperlinkStyle):
        return None
    return sanitize_terminal_hyperlink(style.destination)


def append_style_preserving_hyperlink(style: str, extra_style: str) -> str:
    """追加视觉样式并保留已有的终端链接目标。"""
    combined = f"{str(style or '')} {extra_style}".strip()
    return terminal_hyperlink_style(
        combined,
        terminal_hyperlink_from_style(style),
    )


def strip_terminal_hyperlink_style(style: str) -> str:
    """移除样式中的终端链接元数据。"""
    return str(style or "")


def decorate_scrollback_hyperlinks(parts: FormattedText) -> FormattedText:
    """在最终滚屏批次中生成逐片段闭合的终端链接序列。"""
    out: FormattedText = []

    for style, text in parts:
        destination = terminal_hyperlink_from_style(style)
        visible_style = strip_terminal_hyperlink_style(style)
        if destination and text:
            out.extend((
                ("[ZeroWidthEscape]", _osc8_open(destination)),
                (visible_style, text),
                ("[ZeroWidthEscape]", OSC8_CLOSE),
            ))
        else:
            out.append((visible_style, text))

    return out


class TerminalHyperlinkWindow(Window):
    """在窗口完成布局后为可见链接单元附加完整控制序列。"""

    def _apply_style(
        self,
        screen: Screen,
        write_position: _ScreenWritePosition,
        parent_style: str,
    ) -> None:
        destinations = _screen_hyperlink_destinations(
            screen,
            write_position,
        )
        super()._apply_style(
            screen,
            typing.cast(typing.Any, write_position),
            parent_style,
        )
        _decorate_screen_cells(screen, destinations)


class TerminalHyperlinkOutput(Output):
    """仅放行内部生成且结构完整的终端链接单元。"""

    def __init__(self, output: Output) -> None:
        self._output = output
        self.stdout = output.stdout

    @property
    def vt100_output(self) -> Output:
        """返回用于能力识别的 VT 输出对象。"""
        return getattr(self._output, "vt100_output", self._output)

    @property
    def responds_to_cpr(self) -> bool:
        return self._output.responds_to_cpr

    def __getattr__(self, name: str) -> typing.Any:
        return getattr(self._output, name)

    def fileno(self) -> int:
        return self._output.fileno()

    def encoding(self) -> str:
        return self._output.encoding()

    def write(self, data: str) -> None:
        if _is_safe_decorated_cell(data):
            self._output.write_raw(data)
            return None
        self._output.write(data)

    def write_raw(self, data: str) -> None:
        self._output.write_raw(data)

    def set_title(self, title: str) -> None:
        self._output.set_title(title)

    def clear_title(self) -> None:
        self._output.clear_title()

    def flush(self) -> None:
        self._output.flush()

    def erase_screen(self) -> None:
        self._output.erase_screen()

    def enter_alternate_screen(self) -> None:
        self._output.enter_alternate_screen()

    def quit_alternate_screen(self) -> None:
        self._output.quit_alternate_screen()

    def enable_mouse_support(self) -> None:
        self._output.enable_mouse_support()

    def disable_mouse_support(self) -> None:
        self._output.disable_mouse_support()

    def erase_end_of_line(self) -> None:
        self._output.erase_end_of_line()

    def erase_down(self) -> None:
        self._output.erase_down()

    def reset_attributes(self) -> None:
        self._output.reset_attributes()

    def set_attributes(self, attrs: Attrs, color_depth: ColorDepth) -> None:
        self._output.set_attributes(attrs, color_depth)

    def disable_autowrap(self) -> None:
        self._output.disable_autowrap()

    def enable_autowrap(self) -> None:
        self._output.enable_autowrap()

    def cursor_goto(self, row: int = 0, column: int = 0) -> None:
        self._output.cursor_goto(row, column)

    def cursor_up(self, amount: int) -> None:
        self._output.cursor_up(amount)

    def cursor_down(self, amount: int) -> None:
        self._output.cursor_down(amount)

    def cursor_forward(self, amount: int) -> None:
        self._output.cursor_forward(amount)

    def cursor_backward(self, amount: int) -> None:
        self._output.cursor_backward(amount)

    def hide_cursor(self) -> None:
        self._output.hide_cursor()

    def show_cursor(self) -> None:
        self._output.show_cursor()

    def set_cursor_shape(self, cursor_shape: CursorShape) -> None:
        self._output.set_cursor_shape(cursor_shape)

    def reset_cursor_shape(self) -> None:
        self._output.reset_cursor_shape()

    def get_size(self) -> Size:
        return self._output.get_size()

    def get_default_color_depth(self) -> ColorDepth:
        return self._output.get_default_color_depth()

    def ask_for_cpr(self) -> None:
        self._output.ask_for_cpr()

    def get_rows_below_cursor_position(self) -> int:
        return self._output.get_rows_below_cursor_position()

    def bell(self) -> None:
        self._output.bell()

    def enable_bracketed_paste(self) -> None:
        self._output.enable_bracketed_paste()

    def disable_bracketed_paste(self) -> None:
        self._output.disable_bracketed_paste()

    def reset_cursor_key_mode(self) -> None:
        self._output.reset_cursor_key_mode()

    def scroll_buffer_to_prompt(self) -> None:
        self._output.scroll_buffer_to_prompt()


def _decorate_screen_cells(
    screen: Screen,
    destinations: dict[tuple[int, int], str]
) -> None:
    """把可见链接单元转换为完整的终端控制序列。"""
    for (row_index, column_index), destination in destinations.items():
        cell = screen.data_buffer[row_index][column_index]
        if not cell.char:
            continue

        decorated = Char(
            f"{_osc8_open(destination)}{cell.char}{OSC8_CLOSE}",
            str(cell.style),
        )
        decorated.width = cell.width
        screen.data_buffer[row_index][column_index] = decorated


def _screen_hyperlink_destinations(
    screen: Screen,
    write_position: _ScreenWritePosition
) -> dict[tuple[int, int], str]:
    """读取指定窗口区域尚未应用父样式的链接目标。"""
    destinations: dict[tuple[int, int], str] = {}

    for row_index in range(
        write_position.ypos,
        write_position.ypos + write_position.height,
    ):
        row = screen.data_buffer.get(row_index)
        if row is None:
            continue

        for column_index in range(
            write_position.xpos,
            write_position.xpos + write_position.width,
        ):
            cell = row.get(column_index)
            if cell is None or not cell.char:
                continue

            destination = terminal_hyperlink_from_style(cell.style)
            if destination:
                destinations[row_index, column_index] = destination

    return destinations


def _osc8_open(destination: str) -> str:
    """生成终端链接开始序列。"""
    return f"{OSC8_PREFIX}{destination}{OSC8_SUFFIX}"


def _is_safe_decorated_cell(data: str) -> bool:
    """判断文本是否为内部生成的完整链接单元。"""
    value = str(data or "")
    if not value.startswith(OSC8_PREFIX) or not value.endswith(OSC8_CLOSE):
        return False

    opening_end = value.find(OSC8_SUFFIX, len(OSC8_PREFIX))
    if opening_end < 0:
        return False

    destination = value[len(OSC8_PREFIX):opening_end]
    visible = value[opening_end + len(OSC8_SUFFIX):-len(OSC8_CLOSE)]

    return bool(
        visible
        and not _contains_terminal_control(visible)
        and sanitize_terminal_hyperlink(destination) == destination
    )


def _contains_terminal_control(value: str) -> bool:
    """判断文本是否包含终端控制字符。"""
    return any(
        ord(character) < 32 or 127 <= ord(character) <= 159
        for character in value
    )


if __name__ == '__main__':
    pass
