# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import sys
import functools
import operator
from infrastructure.errors import AppError
from metadata import const
from mind_app.frontend.contracts import Frontend
from mind_app.interaction import NonInteractiveInteraction
from mind_app.presentation.terminal.contracts import TerminalDesign
from .selection import OutputMode


def stream_is_interactive(stream: object) -> bool:
    """判断一个标准流是否连接到交互终端。"""
    try:
        return bool(operator.methodcaller("isatty")(stream))
    except (AttributeError, OSError, TypeError, ValueError):
        return False


def _require_tui_terminal() -> None:
    """确保 TUI 运行在具备交互输入输出的终端中。"""
    if (
        stream_is_interactive(sys.stdin)
        and stream_is_interactive(sys.stdout)
    ):
        return None
    raise AppError(
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
        from mind_app.tui.features.transcript_export import TranscriptExporter
        from prompt_toolkit.input import create_input
        from mind_app.presentation.terminal.capabilities import detect_terminal_capabilities
        from mind_app.presentation.terminal.progress import create_terminal_progress

        transcript_exporter = TranscriptExporter()
        application_input = create_input(sys.stdin)

        def replay_terminal_input(data: bytes) -> None:
            """将启动探测读到的 POSIX 输入交还 prompt_toolkit 解析器。"""
            parser = getattr(application_input, "vt100_parser", None)
            if parser is not None:
                try:
                    operator.methodcaller("feed", data.decode(
                        sys.stdin.encoding or "utf-8",
                        errors="replace",
                    ))(parser)
                except (AttributeError, TypeError):
                    return None

        runtime = TuiRuntime(
            input_obj=application_input,
            terminal_progress=create_terminal_progress(sys.stdout),
            terminal_capabilities=detect_terminal_capabilities(
                input_stream=sys.stdin,
                output_stream=sys.stdout,
                input_replay=replay_terminal_input,
            ),
            export_transcript=transcript_exporter.export,
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
    from mind_app.output.text import create_text_output_session

    if output_mode == "json":
        return Frontend(
            application=JsonApplicationSink(sys.stdout),
            interaction=NonInteractiveInteraction(),
            session_factory=create_json_output_session,
        )

    application = ConsoleApplicationSink()

    return Frontend(
        application=application,
        interaction=NonInteractiveInteraction(),
        session_factory=create_text_output_session,
    )


def resolve_cli_design(
    _frontend: Frontend,
    output_mode: OutputMode
) -> TerminalDesign | None:
    """按输出模式创建非 TUI 终端设计能力。"""
    if output_mode == "tui":
        return None
    from mind_app.presentation.terminal import TerminalDownloadRenderer

    return TerminalDownloadRenderer()


if __name__ == '__main__':
    pass
