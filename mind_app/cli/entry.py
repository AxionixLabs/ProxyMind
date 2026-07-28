# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import sys
import typing
import asyncio
from engine.errors import AppError
from mind_core.config import ConfigOverride
from .commands import (
    AgentListenCommand,
    CompletionCommand,
    DoctorCommand,
    ExecCommand,
    HelixUpgradeCommand,
    McpAddCommand,
    McpGetCommand,
    McpListCommand,
    McpRemoveCommand,
    McpServerCommand,
    McpSetEnabledCommand,
    ParsedCommand
)
from .parser import parse_cli_invocation

if typing.TYPE_CHECKING:
    from mind_app.frontend.contracts import ApplicationSink


def _entry_application(command: ParsedCommand) -> "ApplicationSink":
    """创建入口异常和退场展示使用的输出端。"""
    from rich.console import Console
    from mind_app.frontend.sinks import (
        ConsoleApplicationSink,
        JsonApplicationSink
    )

    if isinstance(command, McpServerCommand):
        return ConsoleApplicationSink(Console(file=sys.stderr))
    if command_requests_json(command):
        return JsonApplicationSink(sys.stdout)

    return ConsoleApplicationSink()


def command_requests_json(command: ParsedCommand) -> bool:
    """判断命令是否要求入口错误使用 JSON 输出。"""
    return (
        isinstance(command, (
            ExecCommand,
            DoctorCommand,
            McpListCommand,
            McpGetCommand,
        ))
        and command.output_format == "json"
    )


def command_requests_outro(
    command: ParsedCommand,
    *,
    output_stream: object | None = None
) -> bool:
    """判断命令是否需要 Rich 退场展示。"""
    if not isinstance(command, (AgentListenCommand, HelixUpgradeCommand)):
        return False

    from .frontend import stream_is_interactive

    stream = sys.stdout if output_stream is None else output_stream

    return stream_is_interactive(stream)


def emit_entry_outro(
    command: ParsedCommand,
    *,
    output_stream: object | None = None
) -> None:
    """为需要动态展示的命令发送退场视图。"""
    if not command_requests_outro(command, output_stream=output_stream):
        return None

    from mind_app.frontend.contracts import ApplicationView

    _entry_application(command).emit(ApplicationView(type="outro"))


def emit_entry_failure(
    command: ParsedCommand,
    error: object,
    *,
    phase: str
) -> None:
    """按照命令输出契约发送入口失败。"""
    from mind_app.frontend.contracts import ApplicationView

    application = _entry_application(command)

    if command_requests_json(command):
        application.emit(ApplicationView(
            type="json",
            renderable={
                "type": "turn.failed",
                "error": str(error),
                "phase": phase or "runtime",
            },
        ))
        return None

    application.emit(ApplicationView(type="error", renderable=str(error)))


def emit_entry_interruption(command: ParsedCommand) -> None:
    """在 JSON 输出模式下发送入口中断。"""
    if command_requests_json(command):
        emit_entry_failure(command, "interrupted", phase="interrupt")


async def main(
    command: ParsedCommand,
    *,
    entry_file: str | None = None,
    config_overrides: tuple[ConfigOverride, ...] = (),
    config_profile: str | None = None
) -> int:
    """把已解析命令路由到对应的应用组合根。"""
    if isinstance(command, McpServerCommand):
        from mind_app.mcp.server import run_mind_mcp_server

        return await run_mind_mcp_server(
            entry_file=entry_file,
            config_overrides=config_overrides,
            config_profile=config_profile,
        )

    if isinstance(command, CompletionCommand):
        from .completion import run_completion_command

        return run_completion_command(command)

    if isinstance(command, DoctorCommand):
        from .doctor import run_doctor_command

        return run_doctor_command(
            command,
            entry_file=entry_file,
            config_overrides=config_overrides,
            config_profile=config_profile,
        )

    if isinstance(command, (
        McpListCommand,
        McpGetCommand,
        McpAddCommand,
        McpRemoveCommand,
        McpSetEnabledCommand,
    )):
        from .mcp_registry import run_mcp_registry_command

        return run_mcp_registry_command(
            command,
            config_overrides=config_overrides,
            config_profile=config_profile,
        )

    from .bootstrap import run_application

    return await run_application(
        command,
        entry_file=entry_file,
        config_overrides=config_overrides,
        config_profile=config_profile,
    )


def run(
    *,
    entry_file: str | None = None,
    arguments: typing.Sequence[str] | None = None
) -> int:
    """解析命令并运行统一的进程级异步生命周期。"""
    invocation = parse_cli_invocation(arguments)
    command    = invocation.command

    from loguru import logger

    logger.remove()

    try:
        with asyncio.Runner() as runner:
            exit_code = runner.run(main(
                command,
                entry_file=entry_file,
                config_overrides=invocation.config_overrides,
                config_profile=invocation.profile,
            ))
    except AppError as error:
        emit_entry_failure(command, error, phase="runtime")
        emit_entry_outro(command)
        return 1
    except (KeyboardInterrupt, asyncio.CancelledError):
        emit_entry_interruption(command)
        emit_entry_outro(command)
        return 130

    emit_entry_outro(command)
    return exit_code


if __name__ == '__main__':
    pass
