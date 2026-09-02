# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import asyncio
import signal
import sys
import threading
import typing
from types import FrameType

from agent.application import RuntimeServices
from frontends.tui.features.conversation import ConversationCompactorFactory
from infrastructure.config.schema import ConfigOverride
from infrastructure.errors import AppError
from observability import reset_sinks
from .commands import (
    CompletionCommand,
    DoctorCommand,
    ExecCommand,
    McpAddCommand,
    McpGetCommand,
    McpListCommand,
    McpRemoveCommand,
    McpServerCommand,
    McpSetEnabledCommand,
    ParsedCommand,
    SessionArchiveCommand
)
from .dispatch import (
    EnvironmentSnapshotProvider,
    RootTurnRunner,
)
from .parser import parse_cli_invocation

if typing.TYPE_CHECKING:
    from agent.ports.presentation import ApplicationSink
    from .bootstrap import CliApplicationHostFactory

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
        self.interrupt_count: int = 0

    @staticmethod
    def restore(previous: InterruptHandler) -> None:
        """恢复安装前的信号处理器。"""
        if previous is not None:
            signal.signal(signal.SIGINT, previous)

    def _cancel_tasks(self, cancel_all: bool) -> None:
        """首次取消主任务，后续取消当前事件循环的全部任务。"""
        main_task = self.main_task
        if not cancel_all and main_task is not None:
            if not main_task.done():
                main_task.cancel()
            return None

        for task in asyncio.all_tasks(self.loop):
            task.cancel()

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

    def install(self) -> InterruptHandler:
        """在主线程默认信号策略下安装处理器。"""
        if threading.current_thread() is not threading.main_thread():
            return None

        previous = signal.getsignal(signal.SIGINT)
        if previous is not signal.default_int_handler:
            return None

        signal.signal(signal.SIGINT, self.handle)
        return previous


def _entry_application(command: ParsedCommand) -> "ApplicationSink":
    """创建入口异常和退场展示使用的输出端。"""
    from frontends.output.application import (
        ConsoleApplicationSink,
        JsonApplicationSink
    )

    if isinstance(command, McpServerCommand):
        return ConsoleApplicationSink(
            console=sys.stderr,
            error_console=sys.stderr,
        )
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


def emit_entry_failure(
    command: ParsedCommand,
    error: object,
    *,
    phase: str
) -> None:
    """按照命令输出契约发送入口失败。"""
    from agent.ports.presentation import ApplicationView

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
    config_profile: str | None = None,
    runtime_services: RuntimeServices | None = None,
    mcp_server_runner: typing.Callable[..., typing.Awaitable[int]] | None = None,
    turn_runner: RootTurnRunner | None = None,
    environment_snapshot_provider: EnvironmentSnapshotProvider | None = None,
    conversation_compactor_factory: ConversationCompactorFactory | None = None,
    application_host_factory: "CliApplicationHostFactory | None" = None,
) -> int:
    """把已解析命令路由到对应的应用组合根。"""
    if isinstance(command, McpServerCommand):
        if runtime_services is None:
            raise AppError("Agent runtime services are required")
        if mcp_server_runner is None:
            raise AppError("MCP server runner is not configured")

        return await mcp_server_runner(
            entry_file=entry_file,
            config_overrides=config_overrides,
            config_profile=config_profile,
            runtime_services=runtime_services,
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

    if isinstance(command, SessionArchiveCommand):
        from .session_archive import run_session_archive_command

        return run_session_archive_command(command)

    from .bootstrap import run_application

    if runtime_services is None:
        raise AppError("Agent runtime services are required")

    if application_host_factory is None:
        return await run_application(
            command,
            entry_file=entry_file,
            config_overrides=config_overrides,
            config_profile=config_profile,
            runtime_services=runtime_services,
            turn_runner=turn_runner,
            environment_snapshot_provider=environment_snapshot_provider,
            conversation_compactor_factory=conversation_compactor_factory,
        )

    return await run_application(
        command,
        entry_file=entry_file,
        config_overrides=config_overrides,
        config_profile=config_profile,
        runtime_services=runtime_services,
        turn_runner=turn_runner,
        environment_snapshot_provider=environment_snapshot_provider,
        conversation_compactor_factory=conversation_compactor_factory,
        application_host_factory=application_host_factory,
    )


async def _run_main(
    interrupts: _InterruptController,
    command: ParsedCommand,
    *,
    entry_file: str | None,
    config_overrides: tuple[ConfigOverride, ...],
    config_profile: str | None,
    runtime_services: RuntimeServices | None,
    mcp_server_runner: typing.Callable[..., typing.Awaitable[int]] | None,
    turn_runner: RootTurnRunner | None,
    environment_snapshot_provider: EnvironmentSnapshotProvider | None,
    conversation_compactor_factory: ConversationCompactorFactory | None,
    application_host_factory: "CliApplicationHostFactory | None",
) -> int:
    """绑定主任务并进入命令路由。"""
    task = asyncio.current_task()
    if task is None:
        raise RuntimeError("Process task is unavailable")

    interrupts.bind_main_task(task)
    if (
        mcp_server_runner is None
        and turn_runner is None
        and environment_snapshot_provider is None
        and conversation_compactor_factory is None
        and application_host_factory is None
    ):
        return await main(
            command,
            entry_file=entry_file,
            config_overrides=config_overrides,
            config_profile=config_profile,
            runtime_services=runtime_services,
        )
    return await main(
        command,
        entry_file=entry_file,
        config_overrides=config_overrides,
        config_profile=config_profile,
        runtime_services=runtime_services,
        mcp_server_runner=mcp_server_runner,
        turn_runner=turn_runner,
        environment_snapshot_provider=environment_snapshot_provider,
        conversation_compactor_factory=conversation_compactor_factory,
        application_host_factory=application_host_factory,
    )


def run(
    *,
    entry_file: str | None = None,
    arguments: typing.Sequence[str] | None = None,
    runtime_services: RuntimeServices | None = None,
    mcp_server_runner: typing.Callable[..., typing.Awaitable[int]] | None = None,
    turn_runner: RootTurnRunner | None = None,
    environment_snapshot_provider: EnvironmentSnapshotProvider | None = None,
    conversation_compactor_factory: ConversationCompactorFactory | None = None,
    application_host_factory: "CliApplicationHostFactory | None" = None,
) -> int:
    """解析命令并运行统一的进程级异步生命周期。"""
    invocation = parse_cli_invocation(arguments)
    command = invocation.command

    reset_sinks()

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
                runtime_services=runtime_services,
                mcp_server_runner=mcp_server_runner,
                turn_runner=turn_runner,
                environment_snapshot_provider=environment_snapshot_provider,
                conversation_compactor_factory=conversation_compactor_factory,
                application_host_factory=application_host_factory,
            ))
    except AppError as error:
        emit_entry_failure(command, error, phase="runtime")
        return 1
    except (KeyboardInterrupt, asyncio.CancelledError):
        emit_entry_interruption(command)
        return 130
    finally:
        if interrupts is not None:
            interrupts.restore(previous_interrupt_handler)

    return exit_code


if __name__ == '__main__':
    pass
