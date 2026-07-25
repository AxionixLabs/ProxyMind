# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import sys
import functools
from engine.errors import ApplicationError
from mind_nova import const
from mind_app.frontend.contracts import Frontend
from mind_app.interaction import NonInteractiveInteraction
from mind_app.runtime.design import TerminalDesign
from .selection import OutputMode


def stream_is_interactive(stream: object) -> bool:
    """判断一个标准流是否连接到交互终端。"""
    isatty = getattr(stream, "isatty", None)
    if not callable(isatty):
        return False
    try:
        return bool(isatty())
    except (OSError, ValueError):
        return False


def _require_tui_terminal() -> None:
    """确保 TUI 运行在具备交互输入输出的终端中。"""
    if (
        stream_is_interactive(sys.stdin)
        and stream_is_interactive(sys.stdout)
    ):
        return None
    raise ApplicationError(
        f"TUI requires interactive stdin and stdout. Run {const.APP_DESC} "
        "in a terminal "
        f"or use '{const.APP_NAME} exec' for non-interactive execution."
    )


def resolve_cli_frontend(output_mode: OutputMode) -> Frontend:
    """根据输出模式装配命令行前端。"""
    if output_mode == "tui":
        _require_tui_terminal()

        from mind_app.tui.adapters.application import TuiApplicationSink
        from mind_app.tui.adapters.session import create_tui_output_session
        from mind_app.tui.core.runtime import TuiRuntime
        from mind_core.design.terminal_capabilities import detect_terminal_capabilities
        from mind_core.design.terminal_progress import create_terminal_progress

        runtime = TuiRuntime(
            terminal_progress=create_terminal_progress(sys.stdout),
            terminal_capabilities=detect_terminal_capabilities(
                input_stream=sys.stdin,
                output_stream=sys.stdout,
            ),
        )
        return Frontend(
            application=TuiApplicationSink(runtime),
            interaction=runtime,
            session_factory=functools.partial(
                create_tui_output_session,
                runtime=runtime,
            ),
            runtime=runtime,
        )

    from mind_app.frontend.sinks import (
        ConsoleApplicationSink,
        JsonApplicationSink,
    )
    from mind_app.output.jsonl import create_json_output_session
    from mind_app.output.rich import create_rich_output_session
    from mind_app.output.text import create_text_output_session

    if output_mode == "json":
        return Frontend(
            application=JsonApplicationSink(sys.stdout),
            interaction=NonInteractiveInteraction(),
            session_factory=create_json_output_session,
        )

    application = ConsoleApplicationSink()
    if output_mode == "rich":
        session_factory = functools.partial(
            create_rich_output_session,
            console=application.console,
        )
    else:
        session_factory = create_text_output_session

    return Frontend(
        application=application,
        interaction=NonInteractiveInteraction(),
        session_factory=session_factory
    )


def resolve_cli_design(
    frontend: Frontend,
    output_mode: OutputMode,
) -> TerminalDesign | None:
    """按输出模式创建非 TUI 终端设计能力。"""
    if output_mode == "tui":
        return None
    from mind_core.design import Design

    console = getattr(frontend.application, "console", None)
    return Design(console=console)


if __name__ == '__main__':
    pass
