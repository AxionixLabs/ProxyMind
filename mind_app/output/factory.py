# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
from .session import SessionFactory

OutputMode = typing.Literal["tui", "rich", "text", "json"]


def resolve_session_factory(mode: OutputMode) -> SessionFactory:
    """根据输出模式返回对应的会话装配入口。"""
    if mode == "tui":
        from .tui import create_tui_output_session
        return create_tui_output_session
    if mode == "rich":
        from .rich import create_rich_output_session
        return create_rich_output_session
    if mode == "text":
        from .text import create_text_output_session
        return create_text_output_session
    if mode == "json":
        from .jsonl import create_json_output_session
        return create_json_output_session

    raise ValueError(f"Unsupported output mode: {mode}")


def output_mode_uses_animation(mode: OutputMode) -> bool:
    """判断输出模式是否使用终端动态展示。"""
    return mode in {"tui", "rich"}


if __name__ == '__main__':
    pass
