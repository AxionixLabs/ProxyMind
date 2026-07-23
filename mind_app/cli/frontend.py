# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import sys
import functools
from engine.errors import MindError
from mind_nova import const
from mind_app.frontend.contracts import Frontend
from mind_app.interaction import NonInteractiveInteraction
from mind_app.runtime.design import TerminalDesign
from .selection import (
    OutputMode,
    output_mode_uses_animation
)


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
    raise MindError(
        f"TUI requires interactive stdin and stdout. Run {const.APP_DESC} "
        "in a terminal "
        "or use --chat, --fast, or --xtra for non-interactive execution."
    )


def resolve_cli_frontend(output_mode: OutputMode) -> Frontend:
    """根据输出模式装配命令行前端。"""
    if output_mode == "tui":
        _require_tui_terminal()

        from mind_app.tui.adapters.application import TuiApplicationSink
        from mind_app.tui.adapters.session import create_tui_output_session
        from mind_app.tui.core.runtime import TuiRuntime

        runtime = TuiRuntime()
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
        SilentApplicationSink,
    )
    from mind_app.output.jsonl import create_json_output_session
    from mind_app.output.rich import create_rich_output_session
    from mind_app.output.text import create_text_output_session

    session_factory = {
        "rich": create_rich_output_session,
        "text": create_text_output_session,
        "json": create_json_output_session,
    }[output_mode]

    application = (
        SilentApplicationSink()
        if output_mode == "json"
        else ConsoleApplicationSink()
    )

    if output_mode_uses_animation(output_mode):
        session_factory = functools.partial(
            session_factory,
            console=application.console
        )

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
