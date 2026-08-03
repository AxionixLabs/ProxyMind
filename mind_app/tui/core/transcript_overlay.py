# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
import unicodedata
from bisect import bisect_right
from copy import deepcopy
from dataclasses import dataclass
from prompt_toolkit.utils import get_cwidth
from .document import (
    TranscriptBlock,
    TranscriptLiveTail,
    TranscriptSnapshot,
    TuiDocument
)
from .models import (
    FormattedText,
    TranscriptBacktrackRequest
)
from .render import (
    fragments_text,
    iter_formatted_text_units,
    iter_text_unit_ranges,
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


@dataclass(frozen=True, slots=True)
class _CellRows(object):
    """描述一个记录 cell 在稳定视觉行索引中的范围。"""
    cell: TranscriptBlock
    start: int
    content_start: int
    stop: int


@dataclass(frozen=True, slots=True)
class TranscriptRenderKey(object):
    """描述动态记录尾部在当前画面中的缓存身份。"""
    width: int
    revision: int
    stream_continuation: bool
    animation_tick: int | None


class TuiTranscriptOverlay(object):
    """管理完整会话记录的缓存、可见行和滚动位置。"""

    STABLE_CELL_CACHE_LIMIT: typing.Final[int] = 1024

    def __init__(
        self,
        *,
        document: TuiDocument,
        get_width: typing.Callable[[], int],
        get_height: typing.Callable[[], int],
        get_snapshot: typing.Callable[[], TranscriptSnapshot],
        invalidate: typing.Callable[[], None]
    ) -> None:
        self.document    = document
        self._get_width  = get_width
        self._get_height = get_height
        self._get_snapshot = get_snapshot
        self._invalidate = invalidate

        self.active: bool           = False
        self.raw_mode: bool         = False
        self.scroll_offset: int     = 0
        self.follow_bottom: bool    = True
        self.backtrack_active: bool = False

        self.search_editing: bool     = False
        self.search_query: str        = ""
        self.export_status: str       = ""
        self.export_failed: bool      = False
        self.export_in_progress: bool = False
        self._export_request_id: int  = 0

        self._selected_cell: TranscriptBlock | None = None

        self._search_matches: tuple[TranscriptBlock, ...] = ()
        self._search_match_index: int                     = -1
        self._search_revision: int                        = -1

        self._cached_stable_revision: int = -1
        self._cached_width: int           = -1
        self._cached_live_tail_key: TranscriptRenderKey | None = None

        self._cached_stable_cells: tuple[TranscriptBlock, ...] = ()
        self._cached_live_tail_lines: list[FormattedText]      = []
        self._stable_cell_cache: dict[int, _CellRender]        = {}
        self._stable_cell_line_counts: dict[int, int]          = {}
        self._stable_cell_rows: list[_CellRows]                = []
        self._stable_cell_row_stops: list[int]                 = []
        self._stable_cell_rows_by_id: dict[int, _CellRows]     = {}
        self._stable_line_count: int                           = 0

    @property
    def has_backtrack_target(self) -> bool:
        """返回记录中是否存在可重新编辑的用户轮次。"""
        return bool(self._backtrack_cells())

    @property
    def search_result_position(self) -> tuple[int, int]:
        """返回当前搜索结果序号和结果总数。"""
        total = len(self._search_matches)
        current = self._search_match_index + 1 if total else 0
        return current, total

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

        for unit in iter_formatted_text_units(line):
            unit_width = max(0, get_cwidth(fragments_text(unit)))
            if current and used_width + unit_width > width:
                rows.append(current)

                current    = list(continuation)
                used_width = sum(get_cwidth(value) for _style, value in current)

            for style, text in unit:
                if current and current[-1][0] == style:
                    previous_style, previous_text = current[-1]
                    current[-1] = previous_style, previous_text + text
                else:
                    current.append((style, text))
            used_width += unit_width

        rows.append(current)
        return rows

    def open(self) -> None:
        """打开完整会话记录并定位到最新内容。"""
        self.active = True
        self.follow_bottom = True
        self._sync_scroll_offset()
        self._invalidate()

    def close(self) -> None:
        """关闭完整会话记录并清理局部视口。"""
        self.active = False
        self.scroll_offset = 0
        self.follow_bottom = True
        self.backtrack_active = False
        self._selected_cell = None
        self._reset_search()
        self.export_status = ""
        self.export_failed = False
        self.export_in_progress = False
        self._export_request_id += 1
        self._invalidate()

    def fragments(self) -> FormattedText:
        """返回按当前终端宽度折行后的完整记录片段。"""
        return join_formatted_lines(self._all_lines())

    def content_changed(self) -> None:
        """在记录内容变化后维持底部跟随或校正阅读位置。"""
        if not self.active:
            return None
        self._sync_scroll_offset()
        if self.search_query and not self.search_editing:
            self._refresh_search_matches()
        self._invalidate()

    def content_replaced(self) -> None:
        """在完整记录被替换后丢弃依赖旧 cell 身份的缓存。"""
        self._clear_render_cache()
        self.content_changed()

    def toggle_raw_mode(self) -> None:
        """切换完整记录的富文本与无装饰文本表示。"""
        self.raw_mode = not self.raw_mode
        self.export_status = ""
        self.export_failed = False
        self._clear_render_cache()
        self._sync_scroll_offset()
        self._invalidate()

    def _clear_render_cache(self) -> None:
        """清除依赖 cell 身份、宽度或渲染模式的视觉缓存。"""
        self._cached_stable_revision = -1
        self._cached_width = -1
        self._cached_live_tail_key = None
        self._cached_stable_cells = ()
        self._cached_live_tail_lines.clear()
        self._stable_cell_cache.clear()
        self._stable_cell_line_counts.clear()
        self._stable_cell_rows.clear()
        self._stable_cell_row_stops.clear()
        self._stable_cell_rows_by_id.clear()
        self._stable_line_count = 0

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

    def begin_search(self) -> None:
        """开始输入记录搜索词并退出历史编辑状态。"""
        self.backtrack_active = False
        self._selected_cell = None
        self.search_editing = True
        self.search_query = ""
        self._search_matches = ()
        self._search_match_index = -1
        self._search_revision = -1
        self.export_status = ""
        self.export_failed = False
        self._invalidate()

    def set_export_status(
        self,
        message: str,
        *,
        failed: bool,
        request_id: int | None = None
    ) -> None:
        """更新最近一次记录导出的用户反馈。"""
        if request_id is not None and (
            not self.active
            or request_id != self._export_request_id
            or not self.export_in_progress
        ):
            return None
        self.export_status = str(message or "").strip()
        self.export_failed = bool(failed)
        self.export_in_progress = False
        self._invalidate()

    def begin_export(self, output_format: str) -> int | None:
        """开始一次记录导出并拒绝并发重复请求。"""
        if self.export_in_progress:
            return None
        self._export_request_id += 1
        self.export_in_progress = True
        self.export_failed = False
        self.export_status = f"Exporting {output_format}..."
        self._invalidate()
        return self._export_request_id

    def append_search_text(self, text: str) -> None:
        """向当前记录搜索词追加可显示字符。"""
        if not self.search_editing:
            return None
        value = "".join(
            char
            for char in str(text or "")
            if (
                char.isprintable()
                or char == "\u200d"
                or unicodedata.category(char) in {"Mn", "Mc"}
            )
        )
        if not value:
            return None
        self.search_query += value
        self._invalidate()

    def backspace_search(self) -> None:
        """删除记录搜索词末尾的一个字符。"""
        if not self.search_editing or not self.search_query:
            return None
        last_start = tuple(iter_text_unit_ranges(self.search_query))[-1][0]
        self.search_query = self.search_query[:last_start]
        self._invalidate()

    def cancel_search(self) -> None:
        """取消当前搜索输入并清除尚未确认的查询。"""
        if not self.search_editing:
            return None
        self._reset_search()
        self._invalidate()

    def confirm_search(self) -> bool:
        """确认搜索词并跳转到当前视口之后的首个结果。"""
        if not self.search_editing:
            return False

        self.search_query = self.search_query.strip()
        self.search_editing = False
        if not self.search_query:
            self._reset_search()
            self._invalidate()
            return False

        self._sync_cache()
        self._refresh_search_matches(force=True, preserve_selection=False)
        if not self._search_matches:
            self._invalidate()
            return False

        self._search_match_index = self._first_search_match_index()
        self._jump_to_search_match()
        return True

    def step_search(self, direction: int) -> bool:
        """循环跳转到下一个或上一个记录搜索结果。"""
        if self.search_editing or not self.search_query:
            return False

        self._sync_cache()
        self._refresh_search_matches()
        if not self._search_matches:
            self._invalidate()
            return False

        step = 1 if direction >= 0 else -1
        self._search_match_index = (
            self._search_match_index + step
        ) % len(self._search_matches)
        self._jump_to_search_match()
        return True

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
            attachments=tuple(deepcopy(item) for item in cell.attachments),
            extras=deepcopy(cell.extras),
        )

    def _backtrack_cells(self) -> tuple[TranscriptBlock, ...]:
        """返回包含稳定轮次标识和原始输入的用户 cell。"""
        return tuple(
            cell
            for cell in self._get_snapshot().committed_cells
            if (
                cell.kind == "user"
                and cell.turn_id
            )
        )

    def _select_backtrack_cell(self, cell: TranscriptBlock) -> None:
        """选择一个用户 cell 并保证其位于当前视口。"""
        self._reset_search()
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
        """返回选中 cell 在稳定视觉行索引中的半开区间。"""
        selected = self._selected_cell
        if selected is None:
            return None

        for rows in self._stable_cell_rows:
            if rows.cell is selected:
                return rows.content_start, rows.stop

        return None

    def _cell_line_range(
        self,
        cell: TranscriptBlock | None
    ) -> tuple[int, int] | None:
        """返回指定 cell 内容在稳定视觉行索引中的半开区间。"""
        if cell is None:
            return None
        rows = self._stable_cell_rows_by_id.get(id(cell))
        if rows is None or rows.cell is not cell:
            return None
        return rows.content_start, rows.stop

    def _highlight_selection(
        self,
        lines: list[FormattedText],
        *,
        start: int
    ) -> list[FormattedText]:
        """给当前视口内选中的用户输入追加高亮样式。"""
        selected_range  = self._selected_line_range()
        highlight_class = "class:transcript.overlay.selection"

        if selected_range is None:
            selected_range = self._cell_line_range(
                self._selected_search_match()
            )
            highlight_class = "class:transcript.overlay.search-match"

        if selected_range is None:
            return lines

        selected_start, selected_stop = selected_range

        return [
            [
                (
                    style
                    if "class:prompt.kicker" in style.split()
                    else f"{style} {highlight_class}".strip(),
                    text,
                )
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
            self._stable_line_count
            + len(self._cached_live_tail_lines)
        )
        return max(0, line_count - self._window_height())

    def _all_lines(self) -> tuple[FormattedText, ...]:
        """返回按当前宽度缓存的稳定行和动态尾部行。"""
        self._sync_cache()
        return (
            *self._stable_lines(0, self._stable_line_count),
            *self._cached_live_tail_lines,
        )

    def _visible_lines(
        self,
        *,
        start: int,
        count: int
    ) -> list[FormattedText]:
        """从稳定行和动态尾部中提取连续的可见行。"""
        stop         = start + max(0, count)
        stable_count = self._stable_line_count

        out = self._stable_lines(start, min(stop, stable_count))

        if stop > stable_count:
            tail_start = max(0, start - stable_count)
            tail_stop = max(0, stop - stable_count)
            out.extend(self._cached_live_tail_lines[tail_start:tail_stop])

        return out

    def _stable_lines(self, start: int, stop: int) -> list[FormattedText]:
        """按轻量索引渲染稳定记录中的指定视觉行。"""
        start = max(0, int(start))
        stop  = min(self._stable_line_count, max(start, int(stop)))

        if start >= stop:
            return []

        index = bisect_right(self._stable_cell_row_stops, start)

        out: list[FormattedText] = []

        for rows in self._stable_cell_rows[index:]:
            if rows.start >= stop:
                break

            row_start = max(start, rows.start)
            row_stop = min(stop, rows.stop)
            if row_start < rows.content_start:
                out.append([])
                row_start = rows.content_start

            if row_start < row_stop:
                rendered = self._stable_cell_render(
                    rows.cell,
                    width=self._cached_width,
                )
                content_offset = row_start - rows.content_start
                content_stop = row_stop - rows.content_start
                out.extend(rendered.lines[content_offset:content_stop])

        return out

    def _sync_cache(self) -> None:
        """按已提交版本和渲染键同步记录缓存。"""
        width = max(1, self._get_width())
        snapshot = self._get_snapshot()
        live_tail = snapshot.live_tail
        live_tail_key = (
            TranscriptRenderKey(
                width=width,
                revision=live_tail.revision,
                stream_continuation=live_tail.stream_continuation,
                animation_tick=live_tail.animation_tick,
            )
            if live_tail is not None
            else None
        )

        stable_changed = (
            snapshot.committed_revision
            != self._cached_stable_revision
            or width != self._cached_width
            or any(
                not cell.transcript_stable
                for cell in snapshot.committed_cells
            )
        )

        live_tail_changed = (
            live_tail_key != self._cached_live_tail_key
            or stable_changed
        )
        if not stable_changed and not live_tail_changed:
            return None

        if stable_changed:
            if self._stable_cells_only_appended(
                snapshot.committed_cells,
                stable_revision=snapshot.committed_revision,
                width=width,
            ):
                self._append_stable_cells(
                    snapshot.committed_cells[len(self._cached_stable_cells):],
                    width=width,
                )
            else:
                self._rebuild_stable_index(
                    snapshot.committed_cells,
                    width=width,
                )
            self._cached_stable_cells = snapshot.committed_cells
            self._cached_stable_revision = snapshot.committed_revision
        if live_tail_changed:
            self._cached_live_tail_lines = list(
                self._render_live_tail(
                    live_tail,
                    width=width,
                    leading_content=self._stable_line_count > 0,
                )
            )
            self._cached_live_tail_key = live_tail_key

        self._cached_width = width

    def _rebuild_stable_index(
        self,
        cells: tuple[TranscriptBlock, ...],
        *,
        width: int
    ) -> None:
        """重建稳定记录的视觉行范围并限制昂贵渲染缓存。"""
        previous_cache = self._stable_cell_cache

        self._stable_cell_cache       = {}
        self._stable_cell_line_counts = {}
        self._stable_cell_rows        = []
        self._stable_cell_row_stops   = []
        self._stable_cell_rows_by_id  = {}
        self._stable_line_count       = 0

        for cell in cells:
            rendered = self._cell_render_from_cache(
                cell,
                width=width,
                cache=previous_cache,
            )
            self._remember_stable_render(rendered)
            self._index_stable_cell(cell, line_count=len(rendered.lines))

    def _append_stable_cells(
        self,
        cells: tuple[TranscriptBlock, ...],
        *,
        width: int
    ) -> None:
        """把新增稳定 cell 追加到视觉行索引。"""
        for cell in cells:
            rendered = self._stable_cell_render(cell, width=width)
            self._index_stable_cell(cell, line_count=len(rendered.lines))

    def _index_stable_cell(
        self,
        cell: TranscriptBlock,
        *,
        line_count: int
    ) -> None:
        """记录单个稳定 cell 的折行数量和全局行范围。"""
        self._stable_cell_line_counts[id(cell)] = line_count
        if line_count <= 0:
            return None

        start = self._stable_line_count
        content_start = start

        if start > 0 and not cell.stream_continuation:
            content_start += 1

        stop = content_start + line_count
        rows = _CellRows(
            cell=cell,
            start=start,
            content_start=content_start,
            stop=stop,
        )
        self._stable_cell_rows.append(rows)
        self._stable_cell_row_stops.append(stop)
        self._stable_cell_rows_by_id[id(cell)] = rows
        self._stable_line_count = stop

    def _reset_search(self) -> None:
        """清除记录搜索输入、匹配集合和选中位置。"""
        self.search_editing = False
        self.search_query = ""
        self._search_matches = ()
        self._search_match_index = -1
        self._search_revision = -1

    def _refresh_search_matches(
        self,
        *,
        force: bool = False,
        preserve_selection: bool = True
    ) -> None:
        """按稳定记录版本更新搜索结果并尽量保留当前选中项。"""
        query    = self.search_query.casefold()
        snapshot = self._get_snapshot()

        if not query:
            self._search_matches = ()
            self._search_match_index = -1
            self._search_revision = snapshot.committed_revision
            return None
        if not force and snapshot.committed_revision == self._search_revision:
            return None

        selected = self._selected_search_match() if preserve_selection else None

        matches = tuple(
            cell
            for cell in snapshot.committed_cells
            if query in self._searchable_cell_text(cell).casefold()
        )

        self._search_matches = matches
        self._search_revision = snapshot.committed_revision

        selected_index = next(
            (
                index
                for index, cell in enumerate(matches)
                if cell is selected
            ),
            None,
        )

        if selected_index is not None:
            self._search_match_index = selected_index
        elif matches:
            self._search_match_index = min(
                max(0, self._search_match_index),
                len(matches) - 1,
            )
        else:
            self._search_match_index = -1

    def _searchable_cell_text(self, cell: TranscriptBlock) -> str:
        """返回不依赖当前富文本排版的 cell 搜索文本。"""
        if cell.raw_text is not None:
            return cell.raw_text
        return fragments_text(self.document.transcript_cell_fragments(cell))

    def _selected_search_match(self) -> TranscriptBlock | None:
        """返回当前选中的记录搜索结果。"""
        if not (
            self._search_matches
            and 0 <= self._search_match_index < len(self._search_matches)
        ):
            return None
        return self._search_matches[self._search_match_index]

    def _first_search_match_index(self) -> int:
        """返回当前视口起点之后的首个搜索结果序号。"""
        for index, cell in enumerate(self._search_matches):
            line_range = self._cell_line_range(cell)
            if line_range is not None and line_range[1] > self.scroll_offset:
                return index
        return 0

    def _jump_to_search_match(self) -> None:
        """把当前搜索结果定位到视口上部并停止底部跟随。"""
        line_range = self._cell_line_range(self._selected_search_match())
        if line_range is None:
            self._invalidate()
            return None

        start, stop = line_range

        height = self._window_height()

        if start < self.scroll_offset or stop > self.scroll_offset + height:
            self.scroll_offset = max(0, start - height // 3)

        self.scroll_offset = min(self.scroll_offset, self._max_scroll_offset())
        self.follow_bottom = False

        self._invalidate()

    def _stable_cell_render(
        self,
        cell: TranscriptBlock,
        *,
        width: int
    ) -> _CellRender:
        """返回一个稳定 cell 的渲染结果并更新最近使用缓存。"""
        rendered = self._cell_render_from_cache(
            cell,
            width=width,
            cache=self._stable_cell_cache,
        )
        self._stable_cell_cache.pop(id(cell), None)
        self._remember_stable_render(rendered)
        return rendered

    def _cell_render_from_cache(
        self,
        cell: TranscriptBlock,
        *,
        width: int,
        cache: dict[int, _CellRender]
    ) -> _CellRender:
        """从指定缓存读取 cell 渲染结果，失效时重新渲染。"""
        rendered = cache.get(id(cell)) if cell.transcript_stable else None
        if (
            rendered is None
            or rendered.cell is not cell
            or rendered.width != width
        ):
            return _CellRender(
                cell=cell,
                width=width,
                lines=self._render_cell(cell, width=width),
            )

        return rendered

    def _remember_stable_render(self, rendered: _CellRender) -> None:
        """保存最近使用的稳定 cell 渲染结果并执行容量限制。"""
        if not rendered.cell.transcript_stable:
            return None
        self._stable_cell_cache[id(rendered.cell)] = rendered
        self._trim_stable_cell_cache()

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
            and all(
                cell is cached_cell
                for cell, cached_cell in zip(
                    cells,
                    self._cached_stable_cells,
                    strict=False,
                )
            )
            and all(cell.transcript_stable for cell in cells)
        )

    def _render_live_tail(
        self,
        live_tail: TranscriptLiveTail | None,
        *,
        width: int,
        leading_content: bool
    ) -> tuple[FormattedText, ...]:
        """把动态 cell 合成为一个不进入已提交缓存的渲染尾部。"""
        if live_tail is None:
            return ()

        lines = self._render_cells(
            live_tail.cells,
            width=width,
            leading_content=False,
        )

        if (
            leading_content
            and lines
            and not live_tail.stream_continuation
        ):
            return [], *lines

        return lines

    def _render_cells(
        self,
        cells: tuple[TranscriptBlock, ...],
        *,
        width: int,
        leading_content: bool
    ) -> tuple[FormattedText, ...]:
        """渲染一组动态 cell 并组合块间距。"""
        out: list[FormattedText] = []

        has_content = leading_content

        for cell in cells:
            lines = self._render_cell(cell, width=width)
            if not lines:
                continue
            if has_content and not cell.stream_continuation:
                out.append([])
            out.extend(lines)
            has_content = True

        return tuple(out)

    def _trim_stable_cell_cache(self) -> None:
        """只保留最近一段稳定 cell 的昂贵视觉行缓存。"""
        excess = len(self._stable_cell_cache) - self.STABLE_CELL_CACHE_LIMIT
        for key in tuple(self._stable_cell_cache)[:max(0, excess)]:
            self._stable_cell_cache.pop(key, None)

    def _render_cell(
        self,
        cell: TranscriptBlock,
        *,
        width: int
    ) -> tuple[FormattedText, ...]:
        """把单个记录 cell 转换为终端视觉行。"""
        parts = (
            [("", cell.raw_text)]
            if self.raw_mode and cell.raw_text is not None
            else self.document.transcript_cell_fragments(cell)
        )
        if self.raw_mode and cell.raw_text is None:
            parts = [("", fragments_text(parts))]

        out: list[FormattedText] = []

        for line in split_formatted_lines(parts):
            out.extend(self._wrap_line(line, width=width))

        return tuple(out)


if __name__ == '__main__':
    pass
