# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import asyncio
import typing

from prompt_toolkit.application import Application
from prompt_toolkit.application.current import create_app_session
from prompt_toolkit.input.base import Input
from prompt_toolkit.patch_stdout import patch_stdout

from observability import observe
from .terminal_stderr import TerminalStderrGuard

_APPLICATION_EXIT_TIMEOUT_SEC: typing.Final[float] = 2.0
_APPLICATION_CANCEL_TIMEOUT_SEC: typing.Final[float] = 1.0


class TerminalApplication(Application[None]):
    """适配终端尺寸轮询，任务的创建、取消和回收由 Application 持有。

    prompt_toolkit 修复首次轮询丢失尺寸变化后，可移除此覆盖并直接使用 Application。
    """

    async def _poll_output_size(self) -> None:
        """从本次运行的首帧尺寸开始检查，避免首次等待吞掉缩放。"""
        interval = self.terminal_size_polling_interval
        if interval is None:
            return

        # 首帧和后台任务启动之间也可能发生缩放，基线必须来自已渲染尺寸。
        size = self.renderer._last_size
        while True:
            new_size = self.output.get_size()
            if size is not None and new_size != size:
                self._on_resize()
            size = new_size
            await asyncio.sleep(interval)


class ApplicationLifecycle(object):
    """拥有输入 Application 任务，并把异常收束为可等待的失败事件。"""

    def __init__(
        self,
        application: Application[None],
        *,
        clear_pending_input: typing.Callable[[Input], None],
        finish_input: typing.Callable[[], None],
        reset_synchronized_output: typing.Callable[[], None],
    ) -> None:
        self.application = application
        self._clear_pending_input = clear_pending_input
        self._finish_input = finish_input
        self._reset_synchronized_output = reset_synchronized_output

        self._task: asyncio.Task[None] | None = None
        self._error: BaseException | None = None
        self._failure = asyncio.Event()
        self._closing = False

    @property
    def active(self) -> bool:
        """返回 Application 任务是否仍在运行。"""
        return self._task is not None and not self._task.done()

    def reset_for_open(self) -> None:
        """清除上一次会话的错误并重新打开失败边界。"""
        self._closing = False
        self._error = None
        self._failure.clear()

    def mark_closing(self) -> None:
        """标记生命周期进入关闭阶段，忽略迟到的任务回调。"""
        self._closing = True

    def start(self) -> asyncio.Task[None]:
        """创建并注册持久输入 Application 任务。"""
        if self._task is not None and not self._task.done():
            return self._task

        self._task = asyncio.create_task(
            self._run_application(),
            name="tui application",
        )
        self._task.add_done_callback(self._task_done)
        return self._task

    def exception(self) -> BaseException | None:
        """返回当前任务或生命周期已经产生的终止错误。"""
        if self._error is not None:
            return self._error

        task = self._task
        if task is None or not task.done() or task.cancelled():
            return None
        return task.exception()

    def fail(self, error: BaseException) -> None:
        """记录首个致命错误并请求输入 Application 退出。"""
        if self._closing or self._error is not None:
            return None

        self._error = error
        self._failure.set()

        application = self.application
        if not application.is_running or application.is_done:
            self._finish_input()
            return None

        application.exit(exception=error)

    async def wait_failure(self) -> BaseException:
        """等待 Application 致命停止并返回原始错误。"""
        await self._failure.wait()
        return self._error if self._error is not None else EOFError()

    async def stop(self, *, erase: bool) -> None:
        """限时等待退出及取消回收，并恢复终端同步输出状态。"""
        task = self._task
        if task is None:
            return None

        self.mark_closing()
        application = self.application
        application.erase_when_done = erase

        try:
            if application.is_running and not application.is_done:
                application.exit(result=None)

            _, pending = await asyncio.wait(
                (task,), timeout=_APPLICATION_EXIT_TIMEOUT_SEC,
            )
            if pending:
                observe(
                    "tui.application.stop.timeout",
                    level="WARNING",
                    phase="exit",
                    timeout_sec=_APPLICATION_EXIT_TIMEOUT_SEC,
                )
        finally:
            try:
                if not task.done():
                    task.cancel()
                    # wait_for 会继续等待忽略取消的协程，不能用作回收期限。
                    _, pending = await asyncio.wait(
                        (task,), timeout=_APPLICATION_CANCEL_TIMEOUT_SEC,
                    )
                    if pending:
                        observe(
                            "tui.application.stop.timeout",
                            level="ERROR",
                            phase="cancel",
                            timeout_sec=_APPLICATION_CANCEL_TIMEOUT_SEC,
                        )
                        raise TimeoutError(
                            "TUI application did not stop after cancellation"
                        )
            finally:
                if task.done():
                    self._task = None
                application.erase_when_done = False
                self._reset_synchronized_output()

    async def _run_application(self) -> None:
        """运行输入 Application 并安装临时事件循环错误处理器。"""
        application = self.application
        loop = asyncio.get_running_loop()
        previous_exception_handler = loop.get_exception_handler()
        exception_handler = self._handle_event_loop_exception
        loop.set_exception_handler(exception_handler)
        stderr_guard = TerminalStderrGuard.install()

        try:
            with create_app_session(
                input=application.input,
                output=application.output,
            ):
                with patch_stdout(raw=True):
                    await application.run_async(
                        pre_run=lambda: self._clear_pending_input(
                            application.input,
                        ),
                        set_exception_handler=False,
                    )
        except (EOFError, KeyboardInterrupt) as error:
            self._error = error
        finally:
            stderr_guard.close()
            if loop.get_exception_handler() is exception_handler:
                loop.set_exception_handler(previous_exception_handler)

    def _task_done(self, task: asyncio.Task[None]) -> None:
        """记录任务终止状态并唤醒等待方。"""
        task_error = None if task.cancelled() else task.exception()
        if self._closing:
            return None

        if self._error is None:
            self._error = task_error if task_error is not None else EOFError()

        self._failure.set()
        self._finish_input()

    def _handle_event_loop_exception(
        self,
        _loop: asyncio.AbstractEventLoop,
        context: dict[str, typing.Any],
    ) -> None:
        """把事件循环回调异常收束到 Application 失败边界。"""
        error = context.get("exception")
        if not isinstance(error, BaseException):
            message = str(context.get("message") or "Event loop callback failed")
            error = RuntimeError(message)
        self.fail(error)


if __name__ == '__main__':
    pass
