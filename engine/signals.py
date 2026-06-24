# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import sys
import signal
import typing
import asyncio

TaskGetter     = typing.Callable[[], asyncio.Task[typing.Any] | None]
SignalDelegate = typing.Callable[[int | None, typing.Any], None]


class SignalHandler(object):
    """处理进程信号并维护阶段委托函数。"""

    def __init__(self, task_getter: TaskGetter, exit_code: int = 130) -> None:
        """绑定任务获取函数和退出码。"""
        self.task_getter = task_getter
        self.exit_code   = exit_code
        self.count       = 0

        self.delegate: SignalDelegate | None = None

    def bind_delegate(self, delegate: SignalDelegate | None) -> None:
        """绑定后续阶段使用的信号处理函数。"""
        self.delegate = delegate

    def __call__(
        self,
        signum: int | None = None,
        frame: typing.Any = None
    ) -> None:
        """响应信号回调并执行当前阶段处理。"""
        if self.delegate is not None:
            self.delegate(signum, frame)
            return None

        self.count += 1
        if self.count > 1:
            sys.exit(self.exit_code)

        task = self.task_getter()
        if task is not None and not task.done():
            task.cancel()
            return None

        sys.exit(self.exit_code)


def install_handler(task_getter: TaskGetter) -> SignalHandler:
    """安装 SIGINT 处理器并返回处理器实例。"""
    handler = SignalHandler(task_getter)
    signal.signal(signal.SIGINT, handler)
    return handler


def task_interrupt_active() -> bool:
    """返回当前异步任务是否已收到中断请求。"""
    task = asyncio.current_task()
    if task is None:
        return False

    cancelling = getattr(task, "cancelling", None)
    if callable(cancelling):
        return bool(cancelling())

    return task.cancelled()


if __name__ == '__main__':
    pass
