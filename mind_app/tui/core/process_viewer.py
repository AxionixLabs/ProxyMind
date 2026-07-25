# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
import asyncio
from dataclasses import dataclass
from prompt_toolkit.formatted_text import StyleAndTextTuples
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.keys import Keys
from .render import (
    display_line_count,
    sanitize_formatted_text
)

ProcessViewerAction: typing.TypeAlias = typing.Literal[
    "detach",
    "interrupt",
    "exited"
]


@dataclass(frozen=True, slots=True)
class ProcessViewerRequest(object):
    """描述主 TUI 中的进程输出查看内容。"""
    fragments: tuple[tuple[str, str], ...]
    max_height: int = 28


@dataclass(slots=True)
class ProcessViewerState(object):
    """保存进程查看器的内容和等待结果。"""
    request: ProcessViewerRequest
    future: asyncio.Future[typing.Any]


class TuiProcessViewer(object):
    """管理主 TUI Application 内的进程输出查看器。"""

    def __init__(
        self,
        *,
        invalidate: typing.Callable[[], None],
        focus_viewer: typing.Callable[[], None],
        focus_input: typing.Callable[[], None],
        get_width: typing.Callable[[], int],
    ) -> None:
        """初始化查看器的焦点、刷新和按键行为。"""
        self.invalidate   = invalidate
        self.focus_viewer = focus_viewer
        self.focus_input  = focus_input
        self.get_width    = get_width

        self.state: ProcessViewerState | None = None

        self.key_bindings = self._build_key_bindings()

    @property
    def active(self) -> bool:
        """返回当前是否正在查看进程。"""
        return self.state is not None

    async def request(self, request: ProcessViewerRequest) -> typing.Any:
        """显示进程内容并等待用户动作。"""
        future = self.begin(request)

        try:
            return await future
        except BaseException:
            self.settle()
            raise

    def begin(self, request: ProcessViewerRequest) -> asyncio.Future[typing.Any]:
        """同步激活进程查看器并返回等待结果。"""
        if self.state is not None:
            raise RuntimeError("process viewer is already active")

        future = asyncio.get_running_loop().create_future()

        safe_request = ProcessViewerRequest(
            fragments=tuple(sanitize_formatted_text(request.fragments)),
            max_height=request.max_height,
        )
        self.state = ProcessViewerState(request=safe_request, future=future)

        self.focus_viewer()
        self.invalidate()

        return future

    def resolve(self, value: typing.Any) -> None:
        """提交当前查看动作并解除等待。"""
        state = self.state
        if state is not None and not state.future.done():
            state.future.set_result(value)

    def settle(self) -> None:
        """撤下已结束的查看器并恢复主输入焦点。"""
        if self.state is None:
            return None

        self.state = None

        self.focus_input()
        self.invalidate()

    async def close(self) -> None:
        """关闭当前查看器。"""
        self.resolve("detach")
        self.settle()

    def fragments(self) -> StyleAndTextTuples:
        """返回当前查看器的格式化内容。"""
        if self.state is None:
            return []
        return list(self.state.request.fragments)

    def height(self) -> int:
        """返回查看器占用的显示行数。"""
        if self.state is None:
            return 0

        text = "".join(value for _style, value in self.state.request.fragments)
        rows = display_line_count(text, width=max(1, self.get_width()))

        return max(1, min(rows, self.state.request.max_height))

    def _build_key_bindings(self) -> KeyBindings:
        """创建进程查看器的局部按键绑定。"""
        bindings = KeyBindings()

        @bindings.add("enter")
        @bindings.add(Keys.Escape, eager=True)
        @bindings.add("q")
        def _(event) -> None:
            self.resolve("detach")

        @bindings.add("c-c")
        def _(event) -> None:
            self.resolve("interrupt")

        return bindings


if __name__ == '__main__':
    pass
