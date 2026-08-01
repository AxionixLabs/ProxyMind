# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
import asyncio
from dataclasses import dataclass
from prompt_toolkit.formatted_text import StyleAndTextTuples
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.keys import Keys
from prompt_toolkit.styles import Style
from prompt_toolkit.utils import get_cwidth
from mind_app.presentation.terminal_text import sanitize_terminal_text
from .models import (
    MenuOption,
    MenuRequest
)
from .render import (
    clip_fragments,
    clip_text
)

TUI_MENU_STYLE = Style.from_dict({
    "tui-menu.title"        : "bold #DCE6EE",
    "tui-menu.status"       : "#87919D",
    "tui-menu.help"         : "#69727D",
    "tui-menu.index"        : "#8A949F",
    "tui-menu.index.active" : "bg:#1D3A4D #8FC7EA",
    "tui-menu.label"        : "#F4F7FA",
    "tui-menu.label.active" : "bg:#1D3A4D bold #F4F7FA",
    "tui-menu.detail"       : "#7F8C9A",
})


@dataclass(slots=True)
class MenuState(object):
    """保存内嵌菜单的请求、位置和等待结果。"""

    request: MenuRequest
    future: asyncio.Future[typing.Any]
    selected: int


class TuiMenu(object):
    """管理主 TUI Application 内的无边框选择菜单。"""

    VISIBLE_ROWS: typing.Final[int]       = 10
    MIN_LABEL_WIDTH: typing.Final[int]    = 8
    MIN_DETAIL_WIDTH: typing.Final[int]   = 12
    MAX_DETAIL_RESERVE: typing.Final[int] = 24

    def __init__(
        self,
        *,
        invalidate: typing.Callable[[], None],
        focus_menu: typing.Callable[[], None],
        focus_input: typing.Callable[[], None],
        get_width: typing.Callable[[], int]
    ) -> None:
        self.invalidate  = invalidate
        self.focus_menu  = focus_menu
        self.focus_input = focus_input
        self.get_width   = get_width

        self.state: MenuState | None = None

        self.key_bindings = self._build_key_bindings()

    @property
    def active(self) -> bool:
        """返回当前是否存在内嵌菜单。"""
        return self.state is not None

    async def request(self, request: MenuRequest) -> typing.Any:
        """显示菜单并等待用户选择。"""
        request = _sanitize_menu_request(request)
        if not request.options and not request.body:
            return None

        future     = asyncio.get_running_loop().create_future()
        selected   = min(len(request.options) - 1, max(0, request.selected))
        self.state = MenuState(request=request, future=future, selected=selected)

        self.focus_menu()
        self.invalidate()

        try:
            return await future
        finally:
            self.state = None
            self.focus_input()
            self.invalidate()

    async def close(self) -> None:
        """取消当前菜单并恢复输入焦点。"""
        state = self.state
        if state is not None and not state.future.done():
            state.future.set_result(None)
        self.state = None

    def fragments(self) -> StyleAndTextTuples:
        """生成当前菜单可见窗口的格式化片段。"""
        state = self.state
        if state is None:
            return []

        request = state.request
        width   = max(1, self.get_width())

        start, options = self._visible_options(state)

        natural_label_width = max(
            (get_cwidth(option.label) for option in request.options),
            default=0,
        )

        index_width = len(str(max(1, len(request.options))))

        prefix_width = get_cwidth(
            f"  › {str(max(1, len(request.options))).rjust(index_width)}. "
        )

        label_width = self._label_column_width(
            request.options,
            available=max(1, width - prefix_width),
            natural_label_width=natural_label_width,
        )

        out: StyleAndTextTuples = self._header_fragments(request, width=width)

        out.extend([
            ("", "\n"),
            (
                "class:tui-menu.help",
                clip_text(request.help_text, width=width),
            ),
            ("", "\n"),
        ])

        for line in request.body:
            out.extend([
                (
                    "class:tui-menu.detail",
                    f"  {clip_text(line, width=max(1, width - 2))}",
                ),
                ("", "\n"),
            ])

        for offset, option in enumerate(options):
            index  = start + offset
            active = index == state.selected
            marker = "  ›" if active else "   "

            index_style = (
                "class:tui-menu.index.active"
                if active
                else "class:tui-menu.index"
            )

            prefix = f"{marker} {str(index + 1).rjust(index_width)}. "

            row: StyleAndTextTuples = [
                (index_style, f"{marker} {str(index + 1).rjust(index_width)}. "),
            ]

            row.extend(self._option_fragments(
                option,
                available=max(1, width - get_cwidth(prefix)),
                label_width=label_width,
                active=active,
            ))
            out.extend(clip_fragments(row, width=width))
            out.append(("", "\n"))

        return out

    def _header_fragments(
        self,
        request: MenuRequest,
        *,
        width: int,
    ) -> StyleAndTextTuples:
        """生成优先保留标题的单行菜单头部。"""
        title = clip_text(request.title, width=width)

        out: StyleAndTextTuples = [("class:tui-menu.title", title)]

        remaining = width - get_cwidth(title)

        if request.status and remaining > get_cwidth(" · …"):
            status = clip_text(
                f" · {request.status}",
                width=remaining,
            )
            out.append(("class:tui-menu.status", status))

        return out

    def _option_fragments(
        self,
        option: MenuOption,
        *,
        available: int,
        label_width: int | None,
        active: bool,
    ) -> StyleAndTextTuples:
        """按可用宽度分配选项主标签和辅助信息。"""
        label_style = (
            "class:tui-menu.label.active"
            if active
            else "class:tui-menu.label"
        )
        if not option.detail or label_width is None:
            return [(
                label_style,
                clip_text(option.label, width=available),
            )]

        separator_width = get_cwidth(" · ")

        label        = clip_text(option.label, width=label_width)
        padding      = " " * max(0, label_width - get_cwidth(label))
        detail_width = available - label_width - separator_width

        return [
            (label_style, f"{label}{padding}"),
            (
                "class:tui-menu.detail",
                f" · {clip_text(option.detail, width=detail_width)}",
            ),
        ]

    def _label_column_width(
        self,
        options: tuple[MenuOption, ...],
        *,
        available: int,
        natural_label_width: int,
    ) -> int | None:
        """计算全部选项共用的主标签列宽。"""
        detail_width = max(
            (get_cwidth(option.detail) for option in options if option.detail),
            default=0,
        )
        if detail_width <= 0:
            return None

        detail_reserve = min(
            detail_width,
            max(
                self.MIN_DETAIL_WIDTH,
                min(self.MAX_DETAIL_RESERVE, available // 3),
            ),
        )

        max_label_width = available - get_cwidth(" · ") - detail_reserve
        if max_label_width < self.MIN_LABEL_WIDTH:
            return None

        return min(natural_label_width, max_label_width)

    def height(self) -> int:
        """返回当前菜单占用的显示行数。"""
        state = self.state
        if state is None:
            return 0
        option_rows = min(self.VISIBLE_ROWS, len(state.request.options))
        return len(state.request.body) + option_rows + 2

    def update(self, request: MenuRequest) -> None:
        """替换当前菜单或只读面板内容。"""
        state = self.state
        if state is None:
            return None
        request = _sanitize_menu_request(request)
        state.request = request
        if request.options:
            state.selected = min(len(request.options) - 1, state.selected)
        else:
            state.selected = 0
        self.invalidate()

    def finish(self, value: typing.Any) -> None:
        """提交当前菜单结果。"""
        state = self.state
        if state is not None and not state.future.done():
            state.future.set_result(value)

    def _move(self, step: int) -> None:
        """移动当前菜单选择位置。"""
        state = self.state
        if state is None:
            return None
        count = len(state.request.options)
        if count == 0:
            return None
        state.selected = (state.selected + step) % count
        self.invalidate()

    def _choose_index(self, index: int) -> None:
        """按绝对索引提交菜单选项。"""
        state = self.state
        if state is None or not (0 <= index < len(state.request.options)):
            return None
        self.finish(state.request.options[index].value)

    def _visible_options(
        self,
        state: MenuState,
    ) -> tuple[int, tuple[MenuOption, ...]]:
        """返回围绕当前选择位置的菜单窗口。"""
        options = state.request.options
        if len(options) <= self.VISIBLE_ROWS:
            return 0, options

        half  = self.VISIBLE_ROWS // 2
        start = max(0, min(state.selected - half, len(options) - self.VISIBLE_ROWS))

        return start, options[start:start + self.VISIBLE_ROWS]

    def _build_key_bindings(self) -> KeyBindings:
        """创建内嵌菜单局部按键绑定。"""
        bindings = KeyBindings()

        @bindings.add("enter")
        def _(event) -> None:
            state = self.state
            if state is not None and state.request.options:
                self._choose_index(state.selected)
            elif state is not None:
                self.finish(None)

        @bindings.add("down")
        @bindings.add("c-n")
        def _(event) -> None:
            self._move(1)

        @bindings.add("up")
        @bindings.add("c-p")
        def _(event) -> None:
            self._move(-1)

        @bindings.add("pagedown")
        def _(event) -> None:
            self._move(self.VISIBLE_ROWS)

        @bindings.add("pageup")
        def _(event) -> None:
            self._move(-self.VISIBLE_ROWS)

        @bindings.add(Keys.Escape, eager=True)
        @bindings.add("c-c")
        @bindings.add("q")
        def _(event) -> None:
            self.finish(None)

        for number in range(1, 10):
            @bindings.add(str(number))
            def _(event, selected_number=number) -> None:
                state = self.state
                if state is None:
                    return None
                start, options = self._visible_options(state)
                if selected_number <= len(options):
                    self._choose_index(start + selected_number - 1)

        return bindings


def _sanitize_menu_request(request: MenuRequest) -> MenuRequest:
    """复制菜单请求并清理其中的显示字段。"""
    return MenuRequest(
        title=sanitize_terminal_text(request.title),
        options=tuple(
            MenuOption(
                value=option.value,
                label=sanitize_terminal_text(option.label),
                detail=sanitize_terminal_text(option.detail),
            )
            for option in request.options
        ),
        body=tuple(sanitize_terminal_text(line) for line in request.body),
        selected=request.selected,
        status=sanitize_terminal_text(request.status),
        help_text=sanitize_terminal_text(request.help_text),
    )


if __name__ == '__main__':
    pass
