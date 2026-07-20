# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
from engine.errors import MindError
from mind_nova.modes import RunMode

OutputMode = typing.Literal[
    "tui",
    "rich",
    "text",
    "json"
]


def resolve_code_mode(cmd_lines: typing.Any) -> RunMode:
    """返回批处理命令选择的运行模式。"""
    if cmd_lines.chat is not None:
        return "chat"
    if cmd_lines.fast is not None:
        return "fast"
    if cmd_lines.xtra is not None:
        return "xtra"

    raise MindError("--code requires --chat, --fast, or --xtra")


def direct_execution_selected(cmd_lines: typing.Any) -> bool:
    """判断当前命令是否包含直接执行任务。"""
    return bool(
        cmd_lines.chat
        or cmd_lines.fast
        or cmd_lines.xtra
        or cmd_lines.code
    )


def direct_stream_selected(cmd_lines: typing.Any) -> bool:
    """判断当前命令是否包含直接流式请求。"""
    return bool(cmd_lines.chat or cmd_lines.fast or cmd_lines.xtra)


def resolve_cli_output_mode(cmd_lines: typing.Any) -> OutputMode:
    """根据命令入口选择输出模式。"""
    if cmd_lines.json:
        if cmd_lines.code or not direct_stream_selected(cmd_lines):
            raise MindError("--json requires --chat, --fast, or --xtra")
        return "json"
    if cmd_lines.agent:
        return "rich"
    if direct_execution_selected(cmd_lines):
        return "text"

    return "tui"


def output_mode_uses_animation(mode: OutputMode) -> bool:
    """判断命令行输出模式是否使用终端动态展示。"""
    return mode in {"tui", "rich"}


if __name__ == '__main__':
    pass
