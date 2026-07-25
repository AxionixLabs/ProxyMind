# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
from .commands import (
    AgentListenCommand,
    BatchCommand,
    CliCommand,
    DoctorCommand,
    ExecCommand,
    HelixUpgradeCommand,
    InteractiveCommand,
    McpAddCommand,
    McpGetCommand,
    McpListCommand,
    McpRemoveCommand,
    McpSetEnabledCommand,
    ResumeCommand
)

OutputMode = typing.Literal[
    "tui",
    "rich",
    "text",
    "json"
]


def resolve_cli_output_mode(command: CliCommand) -> OutputMode:
    """根据命令入口选择输出模式。"""
    if isinstance(command, DoctorCommand):
        return command.output_format
    if isinstance(command, (McpListCommand, McpGetCommand)):
        return command.output_format
    if isinstance(command, (
        McpAddCommand,
        McpRemoveCommand,
        McpSetEnabledCommand,
    )):
        return "text"
    if isinstance(command, (HelixUpgradeCommand, AgentListenCommand)):
        return "rich"
    if isinstance(command, ExecCommand):
        return command.output_format
    if isinstance(command, BatchCommand):
        return "text"
    if isinstance(command, (InteractiveCommand, ResumeCommand)):
        return "tui"

    typing.assert_never(command)


def output_mode_uses_animation(mode: OutputMode) -> bool:
    """判断命令行输出模式是否使用终端动态展示。"""
    return mode in {"tui", "rich"}


if __name__ == '__main__':
    pass
