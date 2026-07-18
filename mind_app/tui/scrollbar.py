from __future__ import annotations

from collections.abc import Callable
from typing import Any

from prompt_toolkit.layout.controls import BufferControl
from prompt_toolkit.layout.margins import Margin
from prompt_toolkit.mouse_events import MouseEvent, MouseEventType


class StreamBufferControl(BufferControl):
    """在可选择文本的缓冲区上补充滚轮状态通知。"""

    def __init__(
        self,
        *args: Any,
        on_scroll: Callable[[MouseEventType], None],
        **kwargs: Any
    ) -> None:
        super().__init__(*args, **kwargs)
        self._on_scroll = on_scroll

    def mouse_handler(self, mouse_event: MouseEvent):
        """记录滚轮方向并把实际滚动交给窗口处理。"""
        if mouse_event.event_type in {
            MouseEventType.SCROLL_UP,
            MouseEventType.SCROLL_DOWN
        }:
            self._on_scroll(mouse_event.event_type)
        return super().mouse_handler(mouse_event)


class ScrollbarMargin(Margin):
    """绘制不依赖背景色的单列滚动条。"""

    def get_width(self, get_ui_content: Callable[[], Any]) -> int:
        """返回滚动条固定宽度。"""
        return 1

    def create_margin(
        self,
        window_render_info: Any,
        width: int,
        height: int
    ) -> list[tuple[str, str]]:
        """根据正文视口生成轨道和滑块。"""
        content_height = max(0, int(window_render_info.content_height))
        window_height = max(0, int(window_render_info.window_height))
        if height <= 0:
            return []

        if content_height <= window_height or content_height <= 0:
            return [("", " \n") for _ in range(height)]

        visible = min(content_height, max(1, len(window_render_info.displayed_lines)))
        thumb_height = max(1, round(height * visible / content_height))
        travel = max(0, height - thumb_height)
        max_scroll = max(1, content_height - window_height)
        scroll = min(max_scroll, max(0, int(window_render_info.vertical_scroll)))
        thumb_top = round(travel * scroll / max_scroll)

        result: list[tuple[str, str]] = []
        for row in range(height):
            is_thumb = thumb_top <= row < thumb_top + thumb_height
            style = "class:scrollbar.thumb" if is_thumb else "class:scrollbar.track"
            result.append((style, "┃\n" if is_thumb else "│\n"))
        return result
