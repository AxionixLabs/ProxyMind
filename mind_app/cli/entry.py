# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import sys
import signal
import typing
import asyncio
import threading
from types import FrameType
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

InterruptHandler: typing.TypeAlias = (
    typing.Callable[[int, FrameType | None], typing.Any]
    | int
    | signal.Handlers
    | None
)


class _InterruptController(object):
    """把进程中断转换为可控的异步任务取消。"""

    def __init__(self, loop: asyncio.AbstractEventLoop) -> None:
        self.loop = loop
        self.main_task: asyncio.Task[int] | None = None
        self.interrupt_count = 0

    def bind_main_task(self, task: asyncio.Task[int]) -> None:
        """绑定进程级主任务。"""
        self.main_task = task

    def handle(self, _signum: int, _frame: FrameType | None) -> None:
        """在线程安全边界内调度中断处理。"""
        self.interrupt_count += 1
        cancel_all = self.interrupt_count > 1
        try:
            self.loop.call_soon_threadsafe(self._cancel_tasks, cancel_all)
        except RuntimeError:
            return None

    def _cancel_tasks(self, cancel_all: bool) -> None:
        """首次取消主任务，后续取消当前事件循环的全部任务。"""
        main_task = self.main_task
        if not cancel_all and main_task is not None:
            if not main_task.done():
                main_task.cancel()
            return None

        for task in asyncio.all_tasks(self.loop):
            task.cancel()

    def install(self) -> InterruptHandler:
        """在主线程默认信号策略下安装处理器。"""
        if threading.current_thread() is not threading.main_thread():
            return None

        previous = signal.getsignal(signal.SIGINT)
        if previous is not signal.default_int_handler:
            return None

        signal.signal(signal.SIGINT, self.handle)
        return previous

    @staticmethod
    def restore(previous: InterruptHandler) -> None:
        """恢复安装前的信号处理器。"""
        if previous is not None:
            signal.signal(signal.SIGINT, previous)


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


async def _run_main(
    interrupts: _InterruptController,
    command: ParsedCommand,
    *,
    entry_file: str | None,
    config_overrides: tuple[ConfigOverride, ...],
    config_profile: str | None,
) -> int:
    """绑定主任务并进入命令路由。"""
    task = asyncio.current_task()
    if task is None:
        raise RuntimeError("Process task is unavailable")

    interrupts.bind_main_task(task)
    return await main(
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

    interrupts: _InterruptController | None = None
    previous_interrupt_handler: InterruptHandler = None

    try:
        with asyncio.Runner() as runner:
            interrupts = _InterruptController(runner.get_loop())
            previous_interrupt_handler = interrupts.install()
            exit_code = runner.run(_run_main(
                interrupts,
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
    finally:
        if interrupts is not None:
            interrupts.restore(previous_interrupt_handler)

    emit_entry_outro(command)
    return exit_code


if __name__ == '__main__':
    pass
