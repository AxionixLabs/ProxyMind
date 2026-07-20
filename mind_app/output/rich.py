# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

from rich.console import Console
from .terminal_content import TerminalContentSink
from .session import OutputSession
from mind_app.presentation.terminal import TerminalPresentationSink
from mind_app.stream_ui import StreamUI


def create_rich_output_session(
    log_file: str,
    *,
    animate: bool = True,
    console: Console | None = None,
) -> OutputSession:
    """创建 Rich 终端单轮输出会话。"""
    control = (
        StreamUI(log_file, animate=animate, console=console)
        if console is not None
        else StreamUI(log_file, animate=animate)
    )

    return OutputSession(
        control=control,
        content=TerminalContentSink(control),
        presentation=TerminalPresentationSink(control),
    )


if __name__ == '__main__':
    pass
