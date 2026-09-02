# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import asyncio
import contextlib
import typing

from prompt_toolkit.application.current import create_app_session
from prompt_toolkit.patch_stdout import patch_stdout


class ApplicationLifecycle(object):
    """拥有输入 Application 任务，并把异常收束为可等待的失败事件。"""

    def __init__(
        self,
        application: typing.Any,
        *,
        clear_pending_input: typing.Callable[[typing.Any], None],
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
        """停止 Application 任务并恢复终端同步输出状态。"""
        task = self._task
        if task is None:
            return None

        application = self.application
        application.erase_when_done = erase

        if not application.is_done:
            with contextlib.suppress(Exception):
                application.exit(result=None)

        await asyncio.gather(task, return_exceptions=True)
        self._reset_synchronized_output()

        self._task = None
        application.erase_when_done = False

    async def _run_application(self) -> None:
        """运行输入 Application 并安装临时事件循环错误处理器。"""
        application = self.application
        loop = asyncio.get_running_loop()
        previous_exception_handler = loop.get_exception_handler()
        exception_handler = self._handle_event_loop_exception
        loop.set_exception_handler(exception_handler)

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
            if loop.get_exception_handler() is exception_handler:
                loop.set_exception_handler(previous_exception_handler)

    def _task_done(self, task: asyncio.Task[None]) -> None:
        """记录任务终止状态并唤醒等待方。"""
        if self._closing:
            return None

        task_error = None if task.cancelled() else task.exception()
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
