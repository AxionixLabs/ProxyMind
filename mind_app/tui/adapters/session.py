# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

from mind_app.output.session import OutputSession
from mind_app.output.terminal_content import TerminalContentSink
from mind_app.presentation.terminal import TerminalPresentationSink
from ..core.runtime import TuiRuntime
from .output import TuiOutputControl


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
