# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
import asyncio
from dataclasses import dataclass
from prompt_toolkit.formatted_text import StyleAndTextTuples
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.keys import Keys
from prompt_toolkit.styles import Style

from .models import MenuOption, MenuRequest


TUI_MENU_STYLE = Style.from_dict({
    "tui-menu.title": "bold #DCE6EE",
    "tui-menu.status": "#87919D",
    "tui-menu.help": "#69727D",
    "tui-menu.index": "bold #8A949F",
    "tui-menu.index.active": "bold #F4F7FA",
    "tui-menu.label": "bold #F4F7FA",
    "tui-menu.detail": "#7F8C9A",
})


@dataclass(slots=True)
class MenuState(object):
    """保存内嵌菜单的请求、位置和等待结果。"""

    request: MenuRequest
    future: asyncio.Future[typing.Any]
    selected: int


class TuiMenu(object):
    """管理主 TUI Application 内的无边框选择菜单。"""

    VISIBLE_ROWS: typing.Final[int] = 10

    def __init__(
        self,
        *,
        invalidate: typing.Callable[[], None],
        focus_menu: typing.Callable[[], None],
        focus_input: typing.Callable[[], None],
    ) -> None:
        self.invalidate = invalidate
        self.focus_menu = focus_menu
        self.focus_input = focus_input
        self.state: MenuState | None = None
        self.key_bindings = self._build_key_bindings()

    @property
    def active(self) -> bool:
        """返回当前是否存在内嵌菜单。"""
        return self.state is not None

    async def request(self, request: MenuRequest) -> typing.Any:
        """显示菜单并等待用户选择。"""
        if not request.options and not request.body:
            return None
        future = asyncio.get_running_loop().create_future()
        selected = min(len(request.options) - 1, max(0, request.selected))
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
        start, options = self._visible_options(state)
        out: StyleAndTextTuples = [("class:tui-menu.title", request.title)]
        if request.status:
            out.append(("class:tui-menu.status", f" · {request.status}"))
        out.extend([
            ("", "\n"),
            ("class:tui-menu.help", request.help_text),
            ("", "\n"),
        ])
        for line in request.body:
            out.extend([
                ("class:tui-menu.detail", line),
                ("", "\n"),
            ])
        for offset, option in enumerate(options):
            index = start + offset
            active = index == state.selected
            marker = "›" if active else " "
            index_style = (
                "class:tui-menu.index.active"
                if active
                else "class:tui-menu.index"
            )
            out.extend([
                (index_style, f"{marker} {index + 1}. "),
                ("class:tui-menu.label", option.label),
            ])
            if option.detail:
                out.append(("class:tui-menu.detail", f" · {option.detail}"))
            out.append(("", "\n"))
        return out

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
        state.selected = min(count - 1, max(0, state.selected + step))
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
        half = self.VISIBLE_ROWS // 2
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


if __name__ == '__main__':
    pass
