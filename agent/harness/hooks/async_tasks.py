# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import asyncio
import typing
from collections.abc import Coroutine

from agent.ports import HookAsyncTaskOwnerPort
from observability import observe_exception


class HookAsyncTaskOwner(HookAsyncTaskOwnerPort):
    """持有异步 Hook 任务并负责并发限制与关闭收束。"""

    def __init__(self, *, max_concurrency: int = 4) -> None:
        """创建带固定并发上限的后台任务所有者。"""
        if isinstance(max_concurrency, bool) or max_concurrency < 1:
            raise ValueError("Hook async concurrency must be positive")
        self._limit = asyncio.Semaphore(max_concurrency)
        self._tasks: set[asyncio.Task[typing.Any]] = set()
        self._closing = False

    def submit(
        self,
        awaitable: Coroutine[typing.Any, typing.Any, typing.Any],
        *,
        name: str,
    ) -> bool:
        """提交后台 Hook，关闭后拒绝并关闭未启动协程。"""
        if self._closing:
            awaitable.close()
            return False

        task = asyncio.create_task(
            self._run(awaitable),
            name=name,
        )
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        task.add_done_callback(self._observe_failure)
        return True

    async def close(self) -> None:
        """停止接收任务并等待全部后台 Hook 收束。"""
        self._closing = True
        tasks = tuple(self._tasks)
        if not tasks:
            return None

        waiter = asyncio.gather(*tasks, return_exceptions=True)
        try:
            await asyncio.shield(waiter)
        except asyncio.CancelledError:
            await waiter
            raise

    async def _run(
        self,
        awaitable: Coroutine[typing.Any, typing.Any, typing.Any],
    ) -> typing.Any:
        """在并发信号量内执行一个后台 Hook。"""
        async with self._limit:
            return await awaitable

    @staticmethod
    def _observe_failure(task: asyncio.Task[typing.Any]) -> None:
        """记录未被 Hook runtime 消费的后台异常。"""
        if task.cancelled():
            return None
        error = task.exception()
        if error is not None:
            observe_exception(
                "hook.async.failed",
                error,
                level="WARNING",
                task_name=task.get_name(),
            )


if __name__ == '__main__':
    pass
