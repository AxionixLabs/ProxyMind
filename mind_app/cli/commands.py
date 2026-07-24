# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
from dataclasses import dataclass
from mind_nova.modes import (
    DEFAULT_RUN_MODE,
    RunMode
)
from mind_nova.requests.access import (
    AccessMode,
    DEFAULT_ACCESS_MODE
)

OutputFormat = typing.Literal["text", "json"]


@dataclass(frozen=True, slots=True)
class InteractiveCommand(object):
    """描述默认交互式入口。"""
    prompt: str | None = None
    images: tuple[str, ...] = ()
    model: str | None = None


@dataclass(frozen=True, slots=True)
class ExecCommand(object):
    """描述单次无头执行入口。"""
    prompt: str
    mode: RunMode = DEFAULT_RUN_MODE
    access_mode: AccessMode = DEFAULT_ACCESS_MODE
    output_format: OutputFormat = "text"
    helix: bool = False


@dataclass(frozen=True, slots=True)
class BatchCommand(object):
    """描述批量星图执行入口。"""
    sources: tuple[str, ...]
    mode: RunMode
    access_mode: AccessMode = DEFAULT_ACCESS_MODE
    helix: bool = False


@dataclass(frozen=True, slots=True)
class AgentListenCommand(object):
    """描述远端任务订阅入口。"""
    helix: bool = False


@dataclass(frozen=True, slots=True)
class HelixUpgradeCommand(object):
    """描述 Helix 运行组件升级入口。"""


@dataclass(frozen=True, slots=True)
class DoctorCommand(object):
    """描述本地环境诊断入口。"""

    output_format: OutputFormat = "text"


@dataclass(frozen=True, slots=True)
class McpServerCommand(object):
    """描述 stdio MCP 服务入口。"""


RuntimeCommand: typing.TypeAlias = (
    InteractiveCommand
    | ExecCommand
    | BatchCommand
    | AgentListenCommand
)

ApplicationCommand: typing.TypeAlias = RuntimeCommand | HelixUpgradeCommand

CliCommand: typing.TypeAlias = (
    ApplicationCommand
    | DoctorCommand
)

ParsedCommand: typing.TypeAlias = CliCommand | McpServerCommand


def command_uses_helix(command: RuntimeCommand) -> bool:
    """返回命令是否要求启动并接入 Helix。"""
    if isinstance(command, (ExecCommand, BatchCommand, AgentListenCommand)):
        return command.helix
    return False


if __name__ == '__main__':
    pass
