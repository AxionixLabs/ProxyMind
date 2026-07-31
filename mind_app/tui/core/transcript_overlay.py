# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
from dataclasses import dataclass
from prompt_toolkit.utils import get_cwidth
from .document import (
    TranscriptBlock,
    TuiDocument
)
from .models import (
    FormattedText,
    TranscriptBacktrackRequest
)
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
        invalidate: typing.Callable[[], None]
    ) -> None:
        self.document    = document
        self._get_width  = get_width
        self._get_height = get_height
        self._invalidate = invalidate

        self.active: bool           = False
        self.scroll_offset: int     = 0
        self.follow_bottom: bool    = True
        self.backtrack_active: bool = False

        self._selected_cell: TranscriptBlock | None = None

        self._cached_stable_revision: int = -1
        self._cached_active_revision: int = -1
        self._cached_width: int           = -1

        self._cached_stable_cells: tuple[TranscriptBlock, ...] = ()
        self._cached_stable_lines: list[FormattedText]         = []
        self._cached_active_lines: list[FormattedText]         = []
        self._stable_cell_cache: dict[int, _CellRender]        = {}
        self._active_cell_cache: dict[int, _CellRender]        = {}

    @property
    def has_backtrack_target(self) -> bool:
        """返回记录中是否存在可重新编辑的用户轮次。"""
        return bool(self._backtrack_cells())

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

    def open(self) -> None:
        """打开完整会话记录并定位到最新内容。"""
        self.active        = True
        self.follow_bottom = True

        self._sync_scroll_offset()
        self._invalidate()

    def close(self) -> None:
        """关闭完整会话记录并清理局部视口。"""
        self.active           = False
        self.scroll_offset    = 0
        self.follow_bottom    = True
        self.backtrack_active = False
        self._selected_cell   = None

        self._invalidate()

    def fragments(self) -> FormattedText:
        """返回按当前终端宽度折行后的完整记录片段。"""
        return join_formatted_lines(self._all_lines())

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
        visible = self._highlight_selection(
            visible,
            start=self.scroll_offset,
        )
        visible.extend(
            [[("class:transcript.overlay.filler", "~")]]
            * max(0, height - len(visible))
        )

        return join_formatted_lines(visible)

    def begin_or_step_backtrack(self) -> bool:
        """开始历史选择或移动到更早的用户轮次。"""
        cells = self._backtrack_cells()
        if not cells:
            return False

        if not self.backtrack_active or self._selected_cell not in cells:
            target = cells[-1]
        else:
            index  = cells.index(self._selected_cell)
            target = cells[max(0, index - 1)]

        self.backtrack_active = True
        self._select_backtrack_cell(target)

        return True

    def step_backtrack_forward(self) -> bool:
        """移动到更新的用户轮次。"""
        cells = self._backtrack_cells()
        if not self.backtrack_active or self._selected_cell not in cells:
            return False
        index = cells.index(self._selected_cell)
        self._select_backtrack_cell(cells[min(len(cells) - 1, index + 1)])
        return True

    def confirm_backtrack(self) -> TranscriptBacktrackRequest | None:
        """返回当前选择对应的历史编辑请求。"""
        cell = self._selected_cell
        if not self.backtrack_active or cell is None:
            return None

        turn_id = str(cell.turn_id or "").strip()
        if not turn_id:
            return None

        return TranscriptBacktrackRequest(
            turn_id=turn_id,
            prompt=cell.prompt,
        )

    def _backtrack_cells(self) -> tuple[TranscriptBlock, ...]:
        """返回包含稳定轮次标识和原始输入的用户 cell。"""
        return tuple(
            cell
            for cell in self.document.transcript_snapshot().stable_cells
            if (
                cell.kind == "user"
                and cell.turn_id
            )
        )

    def _select_backtrack_cell(self, cell: TranscriptBlock) -> None:
        """选择一个用户 cell 并保证其位于当前视口。"""
        self._selected_cell = cell
        self._sync_cache()

        selected_range = self._selected_line_range()

        if selected_range is not None:
            start, stop = selected_range

            height = self._window_height()

            if start < self.scroll_offset:
                self.scroll_offset = start
            elif stop > self.scroll_offset + height:
                self.scroll_offset = max(0, stop - height)

            self.follow_bottom = self.scroll_offset >= self._max_scroll_offset()

        self._invalidate()

    def _selected_line_range(self) -> tuple[int, int] | None:
        """返回选中 cell 在稳定视觉行缓存中的半开区间。"""
        selected = self._selected_cell
        if selected is None:
            return None

        line: int = 0

        has_content: bool = False

        for cell in self._cached_stable_cells:
            rendered = self._stable_cell_cache.get(id(cell))
            if rendered is None or not rendered.lines:
                continue
            if has_content and cell.gap_before:
                line += 1

            start = line
            line += len(rendered.lines)

            if cell is selected:
                return start, line
            has_content = True

        return None

    def _highlight_selection(
        self,
        lines: list[FormattedText],
        *,
        start: int
    ) -> list[FormattedText]:
        """给当前视口内选中的用户输入追加高亮样式。"""
        selected_range = self._selected_line_range()
        if selected_range is None:
            return lines

        selected_start, selected_stop = selected_range

        return [
            [
                (f"{style} class:transcript.overlay.selection".strip(), text)
                for style, text in line
            ]
            if selected_start <= start + index < selected_stop
            else line
            for index, line in enumerate(lines)
        ]

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


if __name__ == '__main__':
    pass
