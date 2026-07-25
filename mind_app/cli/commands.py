# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
from dataclasses import dataclass
from mind_core.config import ConfigOverride
from mind_nova.modes import (
    DEFAULT_RUN_MODE,
    RunMode
)
from mind_nova.requests.access import (
    AccessMode,
    DEFAULT_ACCESS_MODE
)

OutputFormat = typing.Literal["text", "json"]
McpTransport = typing.Literal["streamable_http", "sse"]


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


@dataclass(frozen=True, slots=True)
class McpListCommand(object):
    """描述外部 MCP 服务列表命令。"""

    output_format: OutputFormat = "text"


@dataclass(frozen=True, slots=True)
class McpGetCommand(object):
    """描述外部 MCP 服务查询命令。"""

    name: str
    output_format: OutputFormat = "text"


@dataclass(frozen=True, slots=True)
class McpAddCommand(object):
    """描述外部 MCP 服务添加命令。"""

    name: str
    url: str | None = None
    transport: McpTransport | None = None
    stdio_command: tuple[str, ...] = ()
    env: tuple[tuple[str, str], ...] = ()
    headers: tuple[tuple[str, str], ...] = ()
    cwd: str | None = None
    enabled: bool = True


@dataclass(frozen=True, slots=True)
class McpRemoveCommand(object):
    """描述外部 MCP 服务删除命令。"""

    name: str


@dataclass(frozen=True, slots=True)
class McpSetEnabledCommand(object):
    """描述外部 MCP 服务启用状态命令。"""

    name: str
    enabled: bool


RuntimeCommand: typing.TypeAlias = (
    InteractiveCommand
    | ExecCommand
    | BatchCommand
    | AgentListenCommand
)

ApplicationCommand: typing.TypeAlias = RuntimeCommand | HelixUpgradeCommand

McpRegistryCommand: typing.TypeAlias = (
    McpListCommand
    | McpGetCommand
    | McpAddCommand
    | McpRemoveCommand
    | McpSetEnabledCommand
)

CliCommand: typing.TypeAlias = (
    ApplicationCommand
    | DoctorCommand
    | McpRegistryCommand
)

ParsedCommand: typing.TypeAlias = CliCommand | McpServerCommand


@dataclass(frozen=True, slots=True)
class CliInvocation(object):
    """描述一次命令及其进程级配置覆盖。"""

    command: ParsedCommand
    config_overrides: tuple[ConfigOverride, ...] = ()


def command_uses_helix(command: RuntimeCommand) -> bool:
    """返回命令是否要求启动并接入 Helix。"""
    if isinstance(command, (ExecCommand, BatchCommand, AgentListenCommand)):
        return command.helix
    return False


if __name__ == '__main__':
    pass
