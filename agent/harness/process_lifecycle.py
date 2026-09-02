# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import asyncio
import typing
from collections.abc import Awaitable

__all__ = ("ProcessLifecycle",)


CleanupResult = typing.TypeVar("CleanupResult")


class ProcessLifecycle:
    """单一持有进程停止信号、退出结果和取消态清理语义。"""

    def __init__(self) -> None:
        """初始化未停止且成功的进程状态。"""
        self._stop_event = asyncio.Event()
        self._exit_code = 0

    @property
    def stop_event(self) -> asyncio.Event:
        """返回进程级协作停止信号。"""
        return self._stop_event

    @property
    def exit_code(self) -> int:
        """返回当前进程退出码。"""
        return self._exit_code

    def set_exit_code(self, value: int) -> None:
        """更新进程退出码。"""
        if not isinstance(value, int) or isinstance(value, bool):
            raise TypeError("exit code must be an integer")
        self._exit_code = value

    def request_stop(self, *, exit_code: int | None = None) -> None:
        """设置协作停止信号，并可原子更新退出码。"""
        if exit_code is not None:
            self.set_exit_code(exit_code)
        self._stop_event.set()

    async def await_cleanup(
        self,
        awaitable: Awaitable[CleanupResult],
    ) -> CleanupResult:
        """在取消态下也等待清理逻辑执行完成。"""
        task = asyncio.ensure_future(awaitable)
        try:
            return await asyncio.shield(task)
        except asyncio.CancelledError:
            current = asyncio.current_task()
            if current is not None and current.cancelling() > 1:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
            else:
                await task
            raise


if __name__ == '__main__':
    pass
