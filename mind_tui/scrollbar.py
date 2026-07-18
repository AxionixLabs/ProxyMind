# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from prompt_toolkit.layout.controls import BufferControl, UIContent, UIControl
from prompt_toolkit.mouse_events import MouseButton, MouseEvent, MouseEventType


@dataclass(frozen=True, slots=True)
class ScrollbarGeometry:
    """表示滚动条在独占列中的几何位置。"""

    track_height: int
    thumb_top: int
    thumb_height: int
    max_scroll: int


def calculate_scrollbar_geometry(
    *,
    content_height: int,
    viewport_height: int,
    track_height: int,
    scroll_offset: int
) -> ScrollbarGeometry | None:
    """根据内容、视口和轨道高度计算滑块位置。"""
    content = max(0, int(content_height))
    viewport = max(0, int(viewport_height))
    track = max(0, int(track_height))
    if track <= 0 or content <= viewport or content <= 0:
        return None

    minimum_thumb = min(track, 3)
    thumb_height = min(
        track,
        max(minimum_thumb, round(track * viewport / content))
    )
    max_scroll = max(1, content - viewport)
    offset = min(max_scroll, max(0, int(scroll_offset)))
    travel = max(0, track - thumb_height)
    thumb_top = round(travel * offset / max_scroll)

    return ScrollbarGeometry(
        track_height=track,
        thumb_top=thumb_top,
        thumb_height=thumb_height,
        max_scroll=max_scroll
    )


class StreamBufferControl(BufferControl):
    """在可选择正文上接管滚轮并转交统一滚动逻辑。"""

    def __init__(
        self,
        *args: Any,
        on_scroll: Callable[[int], None],
        **kwargs: Any
    ) -> None:
        super().__init__(*args, **kwargs)
        self._on_scroll = on_scroll

    def mouse_handler(self, mouse_event: MouseEvent):
        """处理正文区域滚轮事件。"""
        if mouse_event.event_type == MouseEventType.SCROLL_UP:
            self._on_scroll(-3)
            return None
        if mouse_event.event_type == MouseEventType.SCROLL_DOWN:
            self._on_scroll(3)
            return None
        return super().mouse_handler(mouse_event)


class ScrollbarControl(UIControl):
    """绘制并控制贯穿终端高度的单列圆角滚动条。"""

    def __init__(
        self,
        *,
        get_render_info: Callable[[], Any | None],
        on_scroll_by: Callable[[int], None],
        on_scroll_to: Callable[[int], None]
    ) -> None:
        self._get_render_info = get_render_info
        self._on_scroll_by = on_scroll_by
        self._on_scroll_to = on_scroll_to
        self._geometry: ScrollbarGeometry | None = None

    def preferred_width(self, max_available_width: int) -> int | None:
        """返回独占列宽。"""
        return 1

    def create_content(self, width: int, height: int) -> UIContent:
        """按当前正文滚动状态绘制完整终端高度。"""
        info = self._get_render_info()
        if info is None:
            self._geometry = None
        else:
            self._geometry = calculate_scrollbar_geometry(
                content_height=info.content_height,
                viewport_height=info.window_height,
                track_height=height,
                scroll_offset=info.vertical_scroll
            )

        lines = [self._line_fragments(row) for row in range(height)]
        return UIContent(
            get_line=lambda row: lines[row] if 0 <= row < len(lines) else [],
            line_count=height,
            show_cursor=False
        )

    def mouse_handler(self, mouse_event: MouseEvent):
        """处理滚轮、轨道点击和滑块拖拽。"""
        if mouse_event.event_type == MouseEventType.SCROLL_UP:
            self._on_scroll_by(-3)
            return None
        if mouse_event.event_type == MouseEventType.SCROLL_DOWN:
            self._on_scroll_by(3)
            return None
        if mouse_event.event_type == MouseEventType.MOUSE_DOWN:
            self._scroll_to_row(mouse_event.position.y)
            return None
        if (
            mouse_event.event_type == MouseEventType.MOUSE_MOVE
            and mouse_event.button != MouseButton.NONE
        ):
            self._scroll_to_row(mouse_event.position.y)
            return None
        return NotImplemented

    def _scroll_to_row(self, row: int) -> None:
        """把轨道行位置换算成正文滚动偏移。"""
        geometry = self._geometry
        if geometry is None:
            return
        travel = geometry.track_height - geometry.thumb_height
        if travel <= 0:
            self._on_scroll_to(0)
            return
        centered = int(row) - geometry.thumb_height // 2
        position = min(travel, max(0, centered))
        offset = round(geometry.max_scroll * position / travel)
        self._on_scroll_to(offset)

    def _line_fragments(self, row: int) -> list[tuple[str, str]]:
        """返回轨道指定行的字符和样式。"""
        geometry = self._geometry
        if geometry is None:
            return [("", " ")]

        start = geometry.thumb_top
        end = start + geometry.thumb_height - 1
        if row < start or row > end:
            return [("class:scrollbar.track", "┊")]
        if geometry.thumb_height == 1:
            glyph = "●"
        elif row == start:
            glyph = "╮"
        elif row == end:
            glyph = "╯"
        else:
            glyph = "│"
        return [("class:scrollbar.thumb", glyph)]


if __name__ == '__main__':
    pass
