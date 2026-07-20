# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import io
from rich.console import Console
from rich.text import Text
from mind_app.frontend.contracts import (
    ApplicationSink,
    ApplicationView,
    Viewport
)
from mind_core.design import Design
from mind_nova import const

from ..core.runtime import TuiRuntime


class TuiApplicationSink(ApplicationSink):
    """把运行期应用展示写入 TUI 正文状态树。"""

    def __init__(self, runtime: TuiRuntime) -> None:
        self.runtime = runtime
        self.console = Console()

    @property
    def viewport(self) -> Viewport:
        """返回 TUI 当前画布尺寸。"""
        return Viewport(
            width=self.runtime.terminal_width,
            height=self.runtime.terminal_height,
        )

    def emit(self, view: ApplicationView) -> None:
        """在启动前使用控制台，运行期统一写入正文画布。"""
        if not self.runtime.active:
            return self._emit_console(view)

        if view.type == "startup_logo":
            self.runtime.append_block(self._startup_logo())
            return None
        if view.type == "error":
            self.runtime.append_block(
                f"{const.PRINT_HEAD} {const.ERR}{view.renderable or ''}"
            )
            return None
        if view.type == "runtime.update_available":
            local = view.payload.get("local") or {}
            remote = view.payload.get("remote") or {}
            self.runtime.append_block(
                "[bold]Update available[/]\n"
                f"current: {local.get('version') or '-'}\n"
                f"latest: {remote.get('version') or '-'}\n"
                f"notes: {remote.get('notes') or '-'}"
            )
            return None
        if view.type in {"tui.gap", "run.gap", "spacer"}:
            self.runtime.append_gap()
            return None
        if view.renderable is None:
            return None
        self.runtime.append_block(view.renderable)

    def _emit_console(self, view: ApplicationView) -> None:
        """在主 TUI 接管终端前输出入口级内容。"""
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
                "\n[bold]Update available[/]\n"
                f"current: {local.get('version') or '-'}\n"
                f"latest: {remote.get('version') or '-'}\n"
                f"notes: {remote.get('notes') or '-'}"
            )
            return None
        self.console.print(view.renderable or "", end=view.end)

    def _startup_logo(self) -> Text:
        """生成不会直接写入 stdout 的授权标识。"""
        stream = io.StringIO()
        console = Console(
            file=stream,
            width=self.runtime.terminal_width,
            force_terminal=True,
            color_system="truecolor",
        )
        Design.startup_logo(console)
        return Text.from_ansi(stream.getvalue().rstrip())


if __name__ == '__main__':
    pass
