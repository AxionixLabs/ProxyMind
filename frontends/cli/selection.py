# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing

from .commands import (
    AgentListenCommand,
    CliCommand,
    DoctorCommand,
    ExecCommand,
    InteractiveCommand,
    McpAddCommand,
    McpGetCommand,
    McpListCommand,
    McpRemoveCommand,
    McpSetEnabledCommand,
    ResumeCommand,
    RuntimeUpgradeCommand,
    SessionArchiveCommand
)

OutputMode = typing.Literal[
    "tui",
    "text",
    "json"
]


def resolve_cli_output_mode(command: CliCommand) -> OutputMode:
    """根据命令入口选择输出模式。"""
    match command:
        case (DoctorCommand() | McpListCommand() | McpGetCommand()) as output_command:
            return output_command.output_format
        case McpAddCommand() | McpRemoveCommand() | McpSetEnabledCommand():
            return "text"
        case RuntimeUpgradeCommand():
            return "text"
        case SessionArchiveCommand():
            return "text"
        case ExecCommand() as output_command:
            return output_command.output_format
        case AgentListenCommand() | InteractiveCommand() | ResumeCommand():
            return "tui"
        case _ as unreachable:
            typing.assert_never(unreachable)


def output_mode_uses_animation(mode: OutputMode) -> bool:
    """判断命令行输出模式是否使用终端动态展示。"""
    return mode == "tui"


if __name__ == '__main__':
    pass
