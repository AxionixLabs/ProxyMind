# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

from mind_app.presentation.terminal import TerminalPresentationSink
from mind_app.tui.adapters.output import TuiOutputControl
from mind_app.tui.core.runtime import TuiRuntime
from .terminal_content import TerminalContentSink
from .session import OutputSession


def create_tui_output_session(
    log_file: str,
    *,
    animate: bool = True,
    runtime: TuiRuntime,
) -> OutputSession:
    """创建持久终端 TUI 对应的单轮输出会话。"""
    control = TuiOutputControl(
        log_file,
        runtime=runtime,
        animate=animate,
    )
    return OutputSession(
        control=control,
        content=TerminalContentSink(control),
        presentation=TerminalPresentationSink(control),
    )


if __name__ == '__main__':
    pass
