# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
from dataclasses import dataclass

from prompt_toolkit.data_structures import Point
from prompt_toolkit.data_structures import Size
from prompt_toolkit.layout.screen import Char
from prompt_toolkit.layout.screen import Screen
from prompt_toolkit.output import ColorDepth
from prompt_toolkit.output.base import Output
from prompt_toolkit.styles import Attrs

from frontends.terminal.capabilities import (
    TerminalCapabilityState,
    TerminalOutputCapabilities,
)

_SYNCHRONIZED_OUTPUT_BEGIN = "\x1b[?2026h"
_SYNCHRONIZED_OUTPUT_END = "\x1b[?2026l"


@dataclass(frozen=True, slots=True)
class _RasterCell:
    """保存一个物理栅格单元及其宽字符占用事实。"""

    char: str
    style: str
    width: int
    continuation: bool = False


_BLANK_CELL = _RasterCell(" ", "", 1)


class TuiInlineViewportRenderer:
    """以绝对坐标提交单个 prompt_toolkit Screen 栅格。

    ``scroll_lines`` 由 terminal adapter 提供，必须把当前物理画面向上滚动
    恰好指定的行数；renderer 不自行判断平台滚动 API。
    """

    def __init__(
        self,
        output: Output,
        *,
        capabilities: TerminalOutputCapabilities,
        viewport_origin: Point,
        scroll_lines: typing.Callable[[int], None],
        color_depth: ColorDepth = ColorDepth.DEFAULT,
        style_resolver: typing.Callable[[str], Attrs],
    ) -> None:
        if capabilities.absolute_cursor_addressing is not (
            TerminalCapabilityState.SUPPORTED
        ):
            raise ValueError(
                "inline viewport requires absolute cursor addressing"
            )
        if viewport_origin.x < 0 or viewport_origin.y < 0:
            raise ValueError("viewport origin must be non-negative")

        self.output = output
        self.capabilities = capabilities
        self._viewport_origin = viewport_origin
        self._viewport_size = Size(rows=0, columns=0)
        self._last_raster: tuple[tuple[_RasterCell, ...], ...] | None = None
        self._last_zero_width_escapes: tuple[tuple[str, ...], ...] = ()
        self._final_cursor: Point | None = None
        self._terminal_size_generation: int | None = None
        self._terminal_size: Size | None = None
        self._scroll_lines = scroll_lines
        self._color_depth = color_depth
        self._style_resolver = style_resolver
        self._resolved_styles: dict[str, Attrs] = {}

    @property
    def viewport_origin(self) -> Point:
        """返回当前物理 viewport 的左上角。"""

        return self._viewport_origin

    @property
    def viewport_size(self) -> Size:
        """返回当前物理 viewport 的高度和宽度。"""

        return self._viewport_size

    @property
    def last_raster(
        self,
    ) -> tuple[tuple[_RasterCell, ...], ...] | None:
        """返回上一帧的不可变栅格快照。"""

        return self._last_raster

    @property
    def final_cursor(self) -> Point | None:
        """返回上一帧提交的物理最终光标。"""

        return self._final_cursor

    @property
    def terminal_size_generation(self) -> int | None:
        """返回上一帧使用的终端尺寸 generation。"""

        return self._terminal_size_generation

    def render(
        self,
        screen: Screen,
        *,
        cursor_position: Point,
        terminal_size: Size,
        size_generation: int,
    ) -> None:
        """把已布局的 Screen raster 以一次物理帧提交到终端。"""

        if terminal_size.rows <= 0 or terminal_size.columns <= 0:
            raise ValueError("terminal size must be positive")
        if size_generation < 0:
            raise ValueError("terminal size generation must be non-negative")

        frame_width, frame_height = _frame_dimensions(screen, terminal_size)
        previous_origin = self._viewport_origin
        self._viewport_origin, required_scroll = self._fit_viewport_origin(
            terminal_size,
            frame_width,
            frame_height,
        )
        viewport_size = Size(rows=frame_height, columns=frame_width)
        size_changed = self._terminal_size != terminal_size
        generation_changed = (
            self._terminal_size_generation is not None
            and self._terminal_size_generation != size_generation
        )
        reset_frame = (
            self._last_raster is not None
            and (
                size_changed
                or generation_changed
                or self._viewport_origin.x != previous_origin.x
            )
        )

        previous_raster = None if reset_frame else self._last_raster
        previous_escapes = () if reset_frame else self._last_zero_width_escapes
        raster, zero_width_escapes = _snapshot_screen(
            screen,
            width=frame_width,
            height=frame_height,
        )
        synchronized = (
            self.capabilities.synchronized_output
            is TerminalCapabilityState.SUPPORTED
        )

        if synchronized:
            self.output.write_raw(_SYNCHRONIZED_OUTPUT_BEGIN)
            self.output.flush()

        self.output.hide_cursor()
        self.output.disable_autowrap()
        final_cursor: Point | None = None
        try:
            if required_scroll:
                self._scroll_lines(required_scroll)

            if reset_frame:
                self._clear_stale_viewport(terminal_size)

            if previous_raster is None:
                previous_raster = ()
                previous_escapes = ()
                self.output.reset_attributes()
            self._render_diff(
                raster,
                zero_width_escapes,
                previous_raster,
                previous_escapes,
                terminal_size,
            )

            final_cursor = self._resolve_cursor(
                cursor_position,
                terminal_size,
                frame_width,
                frame_height,
            )
            self.output.cursor_goto(
                row=final_cursor.y,
                column=final_cursor.x,
            )
        finally:
            try:
                self.output.reset_attributes()
                self.output.enable_autowrap()
                if screen.show_cursor:
                    self.output.show_cursor()
                self.output.flush()
            finally:
                if synchronized:
                    self.output.write_raw(_SYNCHRONIZED_OUTPUT_END)
                    self.output.flush()

        if final_cursor is None:
            raise RuntimeError("inline viewport did not resolve final cursor")

        self._viewport_size = viewport_size
        self._last_raster = raster
        self._last_zero_width_escapes = zero_width_escapes
        self._final_cursor = final_cursor
        self._terminal_size_generation = size_generation
        self._terminal_size = terminal_size

    def reset(self) -> None:
        """丢弃上一帧栅格和终端尺寸 generation。"""

        self._viewport_size = Size(rows=0, columns=0)
        self._last_raster = None
        self._last_zero_width_escapes = ()
        self._final_cursor = None
        self._terminal_size_generation = None
        self._terminal_size = None

    def set_viewport_origin(self, origin: Point) -> None:
        """更新物理 viewport 起点并使旧坐标栅格失效。"""
        if origin.x < 0 or origin.y < 0:
            raise ValueError("viewport origin must be non-negative")
        if origin == self._viewport_origin:
            return None
        self._viewport_origin = origin
        self.reset()

    def _fit_viewport_origin(
        self,
        terminal_size: Size,
        frame_width: int,
        frame_height: int,
    ) -> tuple[Point, int]:
        """计算 viewport 新起点及需要在事务内提交的滚动行数。"""

        x = min(
            self._viewport_origin.x,
            max(0, terminal_size.columns - frame_width),
        )
        y = max(0, self._viewport_origin.y)
        required_scroll = max(
            0,
            y + frame_height - terminal_size.rows,
        )
        y = max(0, y - required_scroll)
        y = min(y, max(0, terminal_size.rows - frame_height))
        return Point(x=x, y=y), required_scroll

    def _clear_stale_viewport(self, terminal_size: Size) -> None:
        """清除 generation 或尺寸变化后失效的旧 viewport 区域。"""

        old_rows = max(self._viewport_size.rows, 1)
        for row in range(old_rows):
            absolute_row = self._viewport_origin.y + row
            if absolute_row >= terminal_size.rows:
                break
            self.output.cursor_goto(
                row=absolute_row,
                column=self._viewport_origin.x,
            )
            self.output.reset_attributes()
            self.output.erase_end_of_line()

    def _render_diff(
        self,
        raster: tuple[tuple[_RasterCell, ...], ...],
        zero_width_escapes: tuple[tuple[str, ...], ...],
        previous_raster: tuple[tuple[_RasterCell, ...], ...],
        previous_escapes: tuple[tuple[str, ...], ...],
        terminal_size: Size,
    ) -> None:
        """按物理单元差异写入，所有垂直移动都使用绝对坐标。"""

        row_count = min(
            max(len(raster), len(previous_raster)),
            terminal_size.rows - self._viewport_origin.y,
        )
        column_count = min(
            max(
                len(raster[0]) if raster else 0,
                len(previous_raster[0]) if previous_raster else 0,
            ),
            terminal_size.columns - self._viewport_origin.x,
        )
        for row in range(max(0, row_count)):
            row_erased = False
            for column in range(max(0, column_count)):
                new_cell = _snapshot_cell(raster, row, column)
                old_cell = _snapshot_cell(previous_raster, row, column)
                new_escape = _snapshot_escape(
                    zero_width_escapes,
                    row,
                    column,
                )
                old_escape = _snapshot_escape(
                    previous_escapes,
                    row,
                    column,
                )
                if (
                    row_erased
                    and _is_blank_cell(new_cell)
                    and not new_escape
                ):
                    continue
                if new_cell.continuation:
                    continue
                if new_cell == old_cell and new_escape == old_escape:
                    continue

                self.output.cursor_goto(
                    row=self._viewport_origin.y + row,
                    column=self._viewport_origin.x + column,
                )
                if new_escape:
                    self.output.write_raw(new_escape)
                if _is_blank_cell(new_cell):
                    self.output.reset_attributes()
                    self.output.erase_end_of_line()
                    row_erased = True
                    continue
                row_erased = False
                self._write_cell(new_cell)

    def _write_cell(self, cell: _RasterCell) -> None:
        """写入一个非延续物理单元及其样式。"""

        style = cell.style
        attrs = self._resolved_styles.get(style)
        if attrs is None:
            attrs = self._style_resolver(style)
            self._resolved_styles[style] = attrs
        self.output.set_attributes(attrs, self._color_depth)
        self.output.write(cell.char)

    def _resolve_cursor(
        self,
        cursor_position: Point,
        terminal_size: Size,
        frame_width: int,
        frame_height: int,
    ) -> Point:
        """把 raster 内逻辑光标转换为受限的物理绝对坐标。"""

        x = min(max(cursor_position.x, 0), max(0, frame_width - 1))
        y = min(max(cursor_position.y, 0), max(0, frame_height - 1))
        return Point(
            x=min(self._viewport_origin.x + x, terminal_size.columns - 1),
            y=min(self._viewport_origin.y + y, terminal_size.rows - 1),
        )


def _frame_dimensions(screen: Screen, terminal_size: Size) -> tuple[int, int]:
    """计算 raster 在当前物理终端内可提交的宽高。"""

    data_width = 0
    data_height = 0
    for row_index, row in screen.data_buffer.items():
        if row_index < 0:
            continue
        data_height = max(data_height, row_index + 1)
        for column, cell in row.items():
            if column < 0:
                continue
            data_width = max(data_width, column + max(1, cell.width))
    width = min(
        terminal_size.columns,
        max(1, screen.width, data_width),
    )
    height = min(
        terminal_size.rows,
        max(1, screen.height, data_height),
    )
    return width, height


def _snapshot_screen(
    screen: Screen,
    *,
    width: int,
    height: int,
) -> tuple[
    tuple[tuple[_RasterCell, ...], ...],
    tuple[tuple[str, ...], ...],
]:
    """把 Screen 的稀疏单元快照为固定宽高的不可变 raster。"""

    raster: list[tuple[_RasterCell, ...]] = []
    escapes: list[tuple[str, ...]] = []
    for row_index in range(height):
        row_cells = [_BLANK_CELL] * width
        row_escapes = [""] * width
        row = screen.data_buffer.get(row_index)
        if row is not None:
            for column, cell in sorted(row.items()):
                if column < 0 or column >= width:
                    continue
                span = min(max(1, cell.width), width - column)
                row_cells[column] = _RasterCell(
                    char=cell.char,
                    style=cell.style,
                    width=cell.width,
                )
                for offset in range(1, span):
                    row_cells[column + offset] = _RasterCell(
                        char="",
                        style=cell.style,
                        width=0,
                        continuation=True,
                    )
        escape_row = screen.zero_width_escapes.get(row_index)
        if escape_row is not None:
            for column, value in escape_row.items():
                if 0 <= column < width:
                    row_escapes[column] = value
        raster.append(tuple(row_cells))
        escapes.append(tuple(row_escapes))
    return tuple(raster), tuple(escapes)


def _snapshot_cell(
    raster: tuple[tuple[_RasterCell, ...], ...],
    row: int,
    column: int,
) -> _RasterCell:
    """读取快照单元，越界位置视为空白。"""

    if row < 0 or row >= len(raster):
        return _BLANK_CELL
    cells = raster[row]
    if column < 0 or column >= len(cells):
        return _BLANK_CELL
    return cells[column]


def _snapshot_escape(
    escapes: tuple[tuple[str, ...], ...],
    row: int,
    column: int,
) -> str:
    """读取快照中的零宽序列，越界位置视为空。"""

    if row < 0 or row >= len(escapes):
        return ""
    values = escapes[row]
    if column < 0 or column >= len(values):
        return ""
    return values[column]


def _is_blank_cell(cell: _RasterCell) -> bool:
    """判断单元是否可以通过行尾清理移除。"""

    return bool(
        not cell.continuation
        and cell.char == " "
        and not cell.style
    )


if __name__ == '__main__':
    pass
