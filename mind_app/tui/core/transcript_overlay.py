# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
from dataclasses import dataclass
from prompt_toolkit.utils import get_cwidth
from .document import (
    TranscriptBlock,
    TuiDocument
)
from .models import FormattedText
from .render import (
    join_formatted_lines,
    split_formatted_lines
)
from .styles import ASSISTANT_PREFIX_CLASS


@dataclass(slots=True)
class _CellRender(object):
    """缓存一个记录 cell 在指定宽度下的视觉行。"""
    cell: TranscriptBlock
    width: int
    lines: tuple[FormattedText, ...]


class TuiTranscriptOverlay(object):
    """管理完整会话记录的缓存、可见行和滚动位置。"""

    def __init__(
        self,
        *,
        document: TuiDocument,
        get_width: typing.Callable[[], int],
        get_height: typing.Callable[[], int],
        invalidate: typing.Callable[[], None],
    ) -> None:
        self.document    = document
        self._get_width  = get_width
        self._get_height = get_height
        self._invalidate = invalidate

        self.active: bool        = False
        self.scroll_offset: int  = 0
        self.follow_bottom: bool = True

        self._cached_stable_revision: int = -1
        self._cached_active_revision: int = -1
        self._cached_width: int           = -1

        self._cached_stable_cells: tuple[TranscriptBlock, ...] = ()
        self._cached_stable_lines: list[FormattedText]         = []
        self._cached_active_lines: list[FormattedText]         = []
        self._stable_cell_cache: dict[int, _CellRender]        = {}
        self._active_cell_cache: dict[int, _CellRender]        = {}

    def open(self) -> None:
        """打开完整会话记录并定位到最新内容。"""
        self.active        = True
        self.follow_bottom = True

        self._sync_scroll_offset()
        self._invalidate()

    def close(self) -> None:
        """关闭完整会话记录并清理局部视口。"""
        self.active        = False
        self.scroll_offset = 0
        self.follow_bottom = True

        self._invalidate()

    def fragments(self) -> FormattedText:
        """返回按当前终端宽度折行后的完整记录片段。"""
        return join_formatted_lines(self._all_lines())

    def visible_fragments(self) -> FormattedText:
        """返回当前视口内的记录片段并填充未使用的行。"""
        height = max(0, self._get_height())
        if height <= 0:
            return []

        self._sync_scroll_offset()
        visible = self._visible_lines(
            start=self.scroll_offset,
            count=height,
        )
        visible.extend(
            [[("class:transcript.overlay.filler", "~")]]
            * max(0, height - len(visible))
        )
        return join_formatted_lines(visible)

    def content_changed(self) -> None:
        """在记录内容变化后维持底部跟随或校正阅读位置。"""
        if not self.active:
            return None
        self._sync_scroll_offset()
        self._invalidate()

    def scroll_line(self, direction: int) -> None:
        """按视觉行滚动完整会话记录。"""
        self._scroll(direction, 1)

    def scroll_page(self, direction: int) -> None:
        """按当前记录窗口高度翻页。"""
        self._scroll(direction, self._window_height())

    def scroll_half_page(self, direction: int) -> None:
        """按当前记录窗口的一半高度翻页。"""
        self._scroll(direction, max(1, (self._window_height() + 1) // 2))

    def jump_top(self) -> None:
        """跳转到完整会话记录开头。"""
        self.scroll_offset = 0
        self.follow_bottom = False
        self._invalidate()

    def jump_bottom(self) -> None:
        """跳转到完整会话记录末尾并恢复跟随。"""
        self.follow_bottom = True
        self._sync_scroll_offset()
        self._invalidate()

    def scroll_percentage(self) -> int:
        """返回当前完整记录视口的滚动百分比。"""
        max_offset = self._max_scroll_offset()
        if max_offset <= 0:
            return 100
        return min(100, round(self.scroll_offset * 100 / max_offset))

    def _scroll(self, direction: int, amount: int) -> None:
        """按给定视觉行数更新记录视口。"""
        max_offset = self._max_scroll_offset()
        current    = max_offset if self.follow_bottom else self.scroll_offset

        target = max(
            0,
            min(max_offset, current + int(direction) * max(1, amount)),
        )

        self.scroll_offset = target
        self.follow_bottom = target >= max_offset
        self._invalidate()

    def _sync_scroll_offset(self) -> None:
        """根据当前内容和跟随状态校正顶部视觉行。"""
        max_offset = self._max_scroll_offset()
        if self.follow_bottom:
            self.scroll_offset = max_offset
        else:
            self.scroll_offset = min(self.scroll_offset, max_offset)

    def _window_height(self) -> int:
        """返回当前记录窗口高度。"""
        return max(1, self._get_height())

    def _max_scroll_offset(self) -> int:
        """返回保证底部不留空白时允许的最大顶部行。"""
        self._sync_cache()
        line_count = (
            len(self._cached_stable_lines)
            + len(self._cached_active_lines)
        )
        return max(0, line_count - self._window_height())

    def _all_lines(self) -> tuple[FormattedText, ...]:
        """返回按当前宽度缓存的稳定行和动态尾部行。"""
        self._sync_cache()
        return (
            *self._cached_stable_lines,
            *self._cached_active_lines,
        )

    def _visible_lines(
        self,
        *,
        start: int,
        count: int
    ) -> list[FormattedText]:
        """从稳定行和动态尾部中提取连续的可见行。"""
        stop = start + max(0, count)
        stable_count = len(self._cached_stable_lines)

        out = list(self._cached_stable_lines[start:min(stop, stable_count)])

        if stop > stable_count:
            active_start = max(0, start - stable_count)
            active_stop = max(0, stop - stable_count)
            out.extend(self._cached_active_lines[active_start:active_stop])

        return out

    def _sync_cache(self) -> None:
        """按当前文档版本和宽度同步稳定行与动态尾部缓存。"""
        width = max(1, self._get_width())

        stable_changed = (
            self.document.stable_transcript_revision
            != self._cached_stable_revision
            or width != self._cached_width
        )

        active_changed = (
            self.document.active_transcript_revision
            != self._cached_active_revision
            or width != self._cached_width
            or stable_changed
        )
        if not stable_changed and not active_changed:
            return None

        snapshot = self.document.transcript_snapshot()

        if stable_changed:
            if self._stable_cells_only_appended(
                snapshot.stable_cells,
                stable_revision=snapshot.stable_revision,
                width=width,
            ):
                appended_cache: dict[int, _CellRender] = {}
                appended_lines = self._render_cells(
                    snapshot.stable_cells[len(self._cached_stable_cells):],
                    width=width,
                    leading_content=bool(self._cached_stable_lines),
                    cache=appended_cache,
                )
                self._stable_cell_cache.update(appended_cache)
                self._cached_stable_lines.extend(appended_lines)
            else:
                self._cached_stable_lines = list(self._render_cells(
                    snapshot.stable_cells,
                    width=width,
                    leading_content=False,
                    cache=self._stable_cell_cache,
                ))
            self._cached_stable_cells = snapshot.stable_cells
            self._cached_stable_revision = snapshot.stable_revision
        if active_changed:
            self._cached_active_lines = list(self._render_cells(
                snapshot.active_cells,
                width=width,
                leading_content=bool(self._cached_stable_lines),
                cache=self._active_cell_cache,
            ))
            self._cached_active_revision = snapshot.active_revision

        self._cached_width = width

    def _stable_cells_only_appended(
        self,
        cells: tuple[TranscriptBlock, ...],
        *,
        stable_revision: int,
        width: int
    ) -> bool:
        """判断稳定记录是否经过一次追加操作增长。"""
        return bool(
            width == self._cached_width
            and stable_revision == self._cached_stable_revision + 1
            and len(cells) > len(self._cached_stable_cells)
        )

    def _render_cells(
        self,
        cells: tuple[TranscriptBlock, ...],
        *,
        width: int,
        leading_content: bool,
        cache: dict[int, _CellRender]
    ) -> tuple[FormattedText, ...]:
        """复用单 cell 视觉行并组合块间距。"""
        out: list[FormattedText]              = []
        current_cache: dict[int, _CellRender] = {}

        has_content = leading_content

        for cell in cells:
            key = id(cell)
            rendered = cache.get(key)
            if (
                rendered is None
                or rendered.cell is not cell
                or rendered.width != width
            ):
                rendered = _CellRender(
                    cell=cell,
                    width=width,
                    lines=self._render_cell(cell, width=width),
                )
            current_cache[key] = rendered
            if not rendered.lines:
                continue
            if has_content and cell.gap_before:
                out.append([])
            out.extend(rendered.lines)
            has_content = True

        cache.clear()
        cache.update(current_cache)
        return tuple(out)

    def _render_cell(
        self,
        cell: TranscriptBlock,
        *,
        width: int
    ) -> tuple[FormattedText, ...]:
        """把单个记录 cell 转换为终端视觉行。"""
        parts = self.document.transcript_cell_fragments(cell)

        out: list[FormattedText] = []

        for line in split_formatted_lines(parts):
            out.extend(self._wrap_line(line, width=width))

        return tuple(out)

    @staticmethod
    def _wrap_line(
        line: FormattedText,
        *,
        width: int
    ) -> list[FormattedText]:
        """按终端宽度折叠一条逻辑行并保留片段样式。"""
        assistant_line = next(
            (style for style, text in line if text),
            "",
        ) == ASSISTANT_PREFIX_CLASS

        continuation = (
            [(ASSISTANT_PREFIX_CLASS, "  ")]
            if assistant_line and width > 2
            else []
        )

        rows: list[FormattedText] = []
        current: FormattedText    = []

        used_width: int = 0

        for style, text in line:
            for char in text:
                char_width = max(0, get_cwidth(char))
                if current and used_width + char_width > width:
                    rows.append(current)
                    current = list(continuation)
                    used_width = sum(get_cwidth(value) for _style, value in current)
                if current and current[-1][0] == style:
                    previous_style, previous_text = current[-1]
                    current[-1] = previous_style, previous_text + char
                else:
                    current.append((style, char))
                used_width += char_width

        rows.append(current)
        return rows


if __name__ == '__main__':
    pass
