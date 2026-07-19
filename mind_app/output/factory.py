# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
from rich.console import Console
from .session import (
    OutputSession,
    SessionFactory
)

OutputMode = typing.Literal["tui", "text", "json"]


def create_output_session(
    log_file: str,
    *,
    animate: bool = True,
    console: Console | None = None,
) -> OutputSession:
    """创建使用当前终端行为的单轮输出会话。"""
    from mind_app.output.legacy_content import LegacyContentSink
    from mind_app.presentation.legacy import LegacyPresentationSink
    from mind_app.stream_ui import StreamUI

    control = (
        StreamUI(log_file, animate=animate, console=console)
        if console is not None
        else StreamUI(log_file, animate=animate)
    )

    return OutputSession(
        control=control,
        content=LegacyContentSink(control),
        presentation=LegacyPresentationSink(control)
    )


def resolve_session_factory(mode: OutputMode) -> SessionFactory:
    """根据输出模式返回对应的会话装配入口。"""
    if mode == "tui":
        return create_output_session
    if mode == "text":
        from .text import create_text_output_session
        return create_text_output_session
    if mode == "json":
        from .jsonl import create_json_output_session
        return create_json_output_session

    raise ValueError(f"Unsupported output mode: {mode}")


if __name__ == '__main__':
    pass
