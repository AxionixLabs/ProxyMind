# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing

from prompt_toolkit.application import Application
from prompt_toolkit.data_structures import Point
from prompt_toolkit.data_structures import Size
from prompt_toolkit.filters import FilterOrBool
from prompt_toolkit.layout import Layout
from prompt_toolkit.layout.mouse_handlers import MouseHandlers
from prompt_toolkit.layout.screen import Screen
from prompt_toolkit.layout.screen import WritePosition
from prompt_toolkit.output.base import Output
from prompt_toolkit.output import ColorDepth
from prompt_toolkit.renderer import Renderer
from prompt_toolkit.renderer import HeightIsUnknownError
from prompt_toolkit.renderer import _StyleStringHasStyleCache
from prompt_toolkit.renderer import _StyleStringToAttrsCache
from prompt_toolkit.styles import Attrs
from prompt_toolkit.styles import BaseStyle

from frontends.terminal.capabilities import TerminalOutputCapabilities
from .inline_viewport import TuiInlineViewportRenderer


class TuiApplicationRenderer(Renderer):
    """复用 prompt_toolkit 布局栅格并由 Mind 提交 inline 物理 viewport。"""

    def __init__(
        self,
        style: BaseStyle,
        output: Output,
        *,
        capabilities: TerminalOutputCapabilities,
        color_depth: ColorDepth,
        viewport_origin: Point,
        scroll_lines: typing.Callable[[int], None],
        full_screen: bool = False,
        mouse_support: FilterOrBool = False,
        cpr_not_supported_callback: typing.Callable[[], None] | None = None,
    ) -> None:
        super().__init__(
            style=style,
            output=output,
            full_screen=full_screen,
            mouse_support=mouse_support,
            cpr_not_supported_callback=cpr_not_supported_callback,
        )
        self.inline_viewport = TuiInlineViewportRenderer(
            output,
            capabilities=capabilities,
            viewport_origin=viewport_origin,
            scroll_lines=scroll_lines,
            color_depth=color_depth,
            style_resolver=self._resolve_style,
        )

    def reset(
        self,
        _scroll: bool = False,
        leave_alternate_screen: bool = True,
    ) -> None:
        """重置 prompt_toolkit 状态并丢弃自有 viewport 栅格。"""
        super().reset(
            _scroll=_scroll,
            leave_alternate_screen=leave_alternate_screen,
        )
        inline_viewport = getattr(self, "inline_viewport", None)
        if inline_viewport is not None:
            inline_viewport.reset()

    def render(
        self,
        app: Application[None],
        layout: Layout,
        is_done: bool = False,
    ) -> None:
        """生成 prompt_toolkit Screen 后提交一个绝对坐标物理帧。"""
        output = self.output

        if self.full_screen and not self._in_alternate_screen:
            self._in_alternate_screen = True
            output.enter_alternate_screen()

        if not self._bracketed_paste_enabled:
            output.enable_bracketed_paste()
            self._bracketed_paste_enabled = True

        if not self._cursor_key_mode_reset:
            output.reset_cursor_key_mode()
            self._cursor_key_mode_reset = True

        needs_mouse_support = self.mouse_support()
        if needs_mouse_support and not self._mouse_support_enabled:
            output.enable_mouse_support()
            self._mouse_support_enabled = True
        elif not needs_mouse_support and self._mouse_support_enabled:
            output.disable_mouse_support()
            self._mouse_support_enabled = False

        size = output.get_size()
        screen = Screen()
        screen.show_cursor = False
        mouse_handlers = MouseHandlers()

        if self.full_screen:
            height = size.rows
        elif is_done:
            height = layout.container.preferred_height(
                size.columns,
                size.rows,
            ).preferred
        else:
            last_height = self._last_screen.height if self._last_screen else 0
            height = max(
                self._min_available_height,
                last_height,
                layout.container.preferred_height(
                    size.columns,
                    size.rows,
                ).preferred,
            )
        height = min(height, size.rows)

        if self._last_size != size:
            self._last_screen = None

        if (
            self.style.invalidation_hash() != self._last_style_hash
            or app.style_transformation.invalidation_hash()
            != self._last_transformation_hash
            or app.color_depth != self._last_color_depth
        ):
            self._last_screen = None
            self._attrs_for_style = None
            self._style_string_has_style = None

        if self._attrs_for_style is None:
            self._attrs_for_style = _StyleStringToAttrsCache(
                self.style.get_attrs_for_style_str,
                app.style_transformation,
            )
        if self._style_string_has_style is None:
            self._style_string_has_style = _StyleStringHasStyleCache(
                self._attrs_for_style,
            )

        self._last_style_hash = self.style.invalidation_hash()
        self._last_transformation_hash = (
            app.style_transformation.invalidation_hash()
        )
        self._last_color_depth = app.color_depth

        layout.container.write_to_screen(
            screen,
            mouse_handlers,
            WritePosition(
                xpos=0,
                ypos=0,
                width=size.columns,
                height=height,
            ),
            parent_style="",
            erase_bg=False,
            z_index=None,
        )
        screen.draw_all_floats()
        if app.exit_style:
            screen.append_style_to_content(app.exit_style)

        viewport_origin = self._viewport_origin(size)
        self.inline_viewport.set_viewport_origin(viewport_origin)
        cursor_position = (
            Point(x=0, y=min(max(height - 1, 0), size.rows - 1))
            if is_done
            else screen.get_cursor_position(layout.current_window)
        )
        self.inline_viewport.render(
            screen,
            cursor_position=cursor_position,
            terminal_size=size,
            size_generation=app.render_counter,
        )

        self._cursor_pos = self.inline_viewport.final_cursor or Point(
            x=0,
            y=0,
        )
        self._last_screen = screen
        self._last_size = size
        self._last_style = None
        self.mouse_handlers = mouse_handlers
        app.layout.visible_windows = screen.visible_windows

        new_cursor_shape = app.cursor.get_cursor_shape(app)
        if (
            self._last_cursor_shape is None
            or self._last_cursor_shape != new_cursor_shape
        ):
            output.set_cursor_shape(new_cursor_shape)
            self._last_cursor_shape = new_cursor_shape

        output.flush()

        if is_done:
            self.reset()

    def _viewport_origin(self, size: Size) -> Point:
        """根据当前 renderer 已知的可用高度确定 layout 的绝对起点。"""
        if self.full_screen:
            return Point(x=0, y=0)
        try:
            rows_above_layout = self.rows_above_layout
        except (HeightIsUnknownError, NotImplementedError):
            rows_above_layout = self.inline_viewport.viewport_origin.y
        return Point(
            x=0,
            y=min(max(rows_above_layout, 0), max(0, size.rows - 1)),
        )

    def _resolve_style(self, style: str) -> Attrs:
        """返回已应用当前转换的 prompt_toolkit 样式属性。"""
        attrs_for_style = self._attrs_for_style
        if attrs_for_style is None:
            raise RuntimeError("style cache is not initialized")
        return attrs_for_style[style]

    def invalidate_inline_viewport(self) -> None:
        """标记外部 scrollback 写入已使当前 inline 栅格失效。"""
        self.inline_viewport.reset()



if __name__ == '__main__':
    pass
