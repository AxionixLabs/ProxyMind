# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

from rich.console import Console
from .contracts import (
    ApplicationSink,
    ApplicationView,
    Viewport
)


class ConsoleApplicationSink(ApplicationSink):
    """通过指定终端控制台输出应用级展示数据。"""

    def __init__(self, console: Console) -> None:
        self.console = console

    @property
    def viewport(self) -> Viewport:
        """返回控制台当前尺寸。"""
        return Viewport(
            width=getattr(self.console, "width", None),
            height=getattr(self.console, "height", None),
        )

    def emit(self, view: ApplicationView) -> None:
        """输出一项应用级终端展示。"""
        if view.type == "json" and isinstance(view.renderable, dict):
            self.console.print_json(data=view.renderable)
            return None
        self.console.print(view.renderable or "", end=view.end)


class SilentApplicationSink(ApplicationSink):
    """忽略不属于单轮事件流的应用级展示。"""

    @property
    def viewport(self) -> Viewport:
        """返回空展示尺寸。"""
        return Viewport()

    def emit(self, view: ApplicationView) -> None:
        """忽略应用级展示数据。"""
        _ = view
        return None


if __name__ == '__main__':
    pass
