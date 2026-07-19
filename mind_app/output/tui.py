# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

from rich.console import Console
from .rich import create_rich_output_session
from .session import OutputSession


def create_tui_output_session(
    log_file: str,
    *,
    animate: bool = True,
    console: Console | None = None,
) -> OutputSession:
    """创建预留 TUI 适配器对应的单轮输出会话。"""
    return create_rich_output_session(
        log_file,
        animate=animate,
        console=console,
    )


if __name__ == '__main__':
    pass
