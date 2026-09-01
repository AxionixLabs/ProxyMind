# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
from frontends.tui.contracts.pager import StaticPagerRequest
from .models import FormattedText
from ..rendering.fragments import (
    join_formatted_lines,
    wrap_formatted_lines
)


class TuiStaticPager(object):
    """管理只读静态页面的内容、折行和滚动位置。"""

    def __init__(
        self,
        *,
        get_width: typing.Callable[[], int],
        get_height: typing.Callable[[], int],
        invalidate: typing.Callable[[], None]
    ) -> None:
        """绑定页面尺寸和重绘入口。"""
        self._get_width = get_width
        self._get_height = get_height
        self._invalidate = invalidate
        self.active: bool = False
        self.title: str = ""
        self.lines: tuple[tuple[tuple[str, str], ...], ...] = ()
        self.scroll_offset: int = 0
        self._cached_width: int = -1
        self._cached_rows: tuple[tuple[tuple[str, str], ...], ...] = ()

    def open(self, request: StaticPagerRequest) -> None:
        """打开静态页面并定位到内容开头。"""
        if self.active:
            raise RuntimeError("static pager is already active")
        self.active = True
        self.title = str(request.title)
        self.lines = tuple(request.lines)
        self.scroll_offset = 0
        self._clear_cache()
        self._invalidate()

    def close(self) -> None:
        """关闭静态页面并释放内容引用。"""
        self.active = False
        self.title = ""
        self.lines = ()
        self.scroll_offset = 0

        self._clear_cache()
        self._invalidate()

    def abort(self) -> None:
        """在全屏切换失败时无重绘地清除页面状态。"""
        self.active = False
        self.title = ""
        self.lines = ()
        self.scroll_offset = 0
        self._clear_cache()

    def visible_fragments(self) -> FormattedText:
        """返回当前窗口可见的格式化页面内容。"""
        rows = self._rows()
        height = max(0, self._get_height())
        maximum = max(0, len(rows) - height)
        self.scroll_offset = min(self.scroll_offset, maximum)
        visible = rows[self.scroll_offset:self.scroll_offset + height]
        display_rows = [list(row) for row in visible]
        display_rows.extend(
            [("class:static-pager.filler", "~")]
            for _index in range(max(0, height - len(display_rows)))
        )

        return join_formatted_lines(display_rows)

    def scroll_lines(self, step: int) -> None:
        """按物理行移动静态页面。"""
        rows = self._rows()
        height = max(0, self._get_height())
        maximum = max(0, len(rows) - height)
        target = max(0, min(maximum, self.scroll_offset + int(step)))

        if target == self.scroll_offset:
            return None
        self.scroll_offset = target
        self._invalidate()

    def scroll_page(self, direction: int) -> None:
        """按当前正文高度翻动静态页面。"""
        self.scroll_lines(int(direction) * max(1, self._get_height()))

    def scroll_half_page(self, direction: int) -> None:
        """按当前正文高度的一半翻动静态页面。"""
        height = max(1, self._get_height())
        self.scroll_lines(int(direction) * ((height + 1) // 2))

    def jump(self, *, to_end: bool) -> None:
        """跳转到静态页面开头或末尾。"""
        maximum = max(0, len(self._rows()) - max(0, self._get_height()))
        target = maximum if to_end else 0

        if target == self.scroll_offset:
            return None
        self.scroll_offset = target
        self._invalidate()

    def scroll_percentage(self) -> int:
        """返回当前静态页面的滚动百分比。"""
        maximum = max(0, len(self._rows()) - max(0, self._get_height()))
        if maximum <= 0:
            return 100
        # Rust 的 f32::round 使用 half-up；Python round 的 ties-to-even
        # 会在 2.5% 等边界少显示 1%。
        return min(100, int(self.scroll_offset * 100 / maximum + 0.5))

    def _rows(self) -> tuple[tuple[tuple[str, str], ...], ...]:
        """返回按当前宽度折行并缓存的物理行。"""
        width = max(1, self._get_width())
        if width == self._cached_width:
            return self._cached_rows

        rows: list[tuple[tuple[str, str], ...]] = []
        for line in self.lines:
            if not line or not any(fragment[1] for fragment in line):
                rows.append(())
                continue
            wrapped = wrap_formatted_lines(list(line), width=width)
            rows.extend(tuple(row) for row in wrapped)

        self._cached_width = width
        self._cached_rows  = tuple(rows)

        return self._cached_rows

    def _clear_cache(self) -> None:
        """清除依赖页面内容和宽度的折行缓存。"""
        self._cached_width = -1
        self._cached_rows  = ()


if __name__ == '__main__':
    pass
