# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import json
import typing
from rich.console import Console
from mind_core.design import Design
from mind_nova import const
from .contracts import (
    ApplicationSink,
    ApplicationView,
    Viewport
)


class ConsoleApplicationSink(ApplicationSink):
    """通过指定终端控制台输出应用级展示数据。"""

    def __init__(self, console: Console | None = None) -> None:
        self.console = console or Console()

    @property
    def viewport(self) -> Viewport:
        """返回控制台当前尺寸。"""
        return Viewport(
            width=getattr(self.console, "width", None),
            height=getattr(self.console, "height", None),
        )

    def emit(self, view: ApplicationView) -> None:
        """输出一项应用级终端展示。"""
        if view.type == "intro":
            Design.show_intro(self.console)
            return None
        if view.type == "outro":
            Design.show_outro(self.console)
            return None
        if view.type == "startup_logo":
            Design.startup_logo(self.console)
            return None
        if view.type == "error":
            self.console.print(const.PRINT_HEAD, f"{const.ERR}{view.renderable}")
            return None
        if view.type == "runtime.update_available":
            local = view.payload.get("local") or {}
            remote = view.payload.get("remote") or {}
            self.console.print(
                f"\n[bold]╭────── update available ──────╮\n"
                f"current:  [bold #AFFFFF]{local.get('version') or '-'}[/]\n"
                f"latest :  [bold #AFFFFF]{remote.get('version') or '-'}[/]\n"
                f"notes  :  [bold #8A8A8A]{remote.get('notes') or '-'}[/]\n"
            )
            return None
        if view.type == "json" and isinstance(view.renderable, dict):
            self.console.print_json(data=view.renderable)
            return None
        self.console.print(view.renderable or "", end=view.end)


class JsonApplicationSink(ApplicationSink):
    """只写出入口级 JSONL 事件。"""

    def __init__(self, stream: typing.TextIO) -> None:
        self.stream = stream

    @property
    def viewport(self) -> Viewport:
        """返回空展示尺寸。"""
        return Viewport()

    def emit(self, view: ApplicationView) -> None:
        """写出 JSON 事件并忽略其他应用展示。"""
        if view.type != "json" or not isinstance(view.renderable, dict):
            return None
        line = json.dumps(
            view.renderable,
            ensure_ascii=False,
            separators=(",", ":"),
            default=str,
        )
        self.stream.write(line + "\n")
        self.stream.flush()


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
