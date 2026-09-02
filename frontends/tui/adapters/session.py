# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

from agent.ports import OutputSession
from .content import TuiContentSink
from .output import TuiOutputControl
from .presentation import TuiPresentationSink
from .status import TuiStreamStatusControl
from ..core.runtime import TuiRuntime


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
    presentation = TuiPresentationSink(control)
    return OutputSession(
        control=control,
        status=TuiStreamStatusControl(),
        content=TuiContentSink(
            control,
            before_assistant_output=(
                presentation.flush_terminal_waits_before_assistant_output
            ),
        ),
        presentation=presentation,
    )


if __name__ == '__main__':
    pass
