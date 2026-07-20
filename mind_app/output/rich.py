# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

from rich.console import Console
from .terminal_content import TerminalContentSink
from .session import OutputSession
from .rich_control import RichOutputControl
from mind_app.presentation.rich.sink import RichPresentationSink


def create_rich_output_session(
    log_file: str,
    *,
    animate: bool = True,
    console: Console | None = None,
) -> OutputSession:
    """创建 Rich 终端单轮输出会话。"""
    control = (
        RichOutputControl(log_file, animate=animate, console=console)
        if console is not None
        else RichOutputControl(log_file, animate=animate)
    )

    return OutputSession(
        control=control,
        content=TerminalContentSink(control),
        presentation=RichPresentationSink(control),
    )


if __name__ == '__main__':
    pass
