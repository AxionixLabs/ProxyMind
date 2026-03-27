# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import asyncio
import typing


AnimRunner: typing.TypeAlias = typing.Callable[[asyncio.Event], typing.Awaitable[None]]


class AsyncAnimManager(object):
    """单实例异步动画管理器。"""

    def __init__(self) -> None:
        self._lock: asyncio.Lock = asyncio.Lock()
        self._stop_event: asyncio.Event | None = None
        self._task: asyncio.Task[None] | None = None

    @property
    def running(self) -> bool:
        """当前是否有动画在运行。"""
        task = self._task
        return task is not None and not task.done()

    def _clear_locked(self) -> None:
        """清空当前动画状态；调用方必须已持有锁。"""
        self._stop_event = None
        self._task = None

    def _prune_finished_locked(self) -> None:
        """回收已经结束的任务引用；调用方必须已持有锁。"""
        task = self._task
        if task is not None and task.done():
            self._clear_locked()

    async def start(self, runner: AnimRunner) -> None:
        """启动动画；启动前会严格停止当前动画。"""
        while True:
            async with self._lock:
                self._prune_finished_locked()
                stop_event, task = self._stop_event, self._task

                if task is None:
                    stop_event = asyncio.Event()
                    task = asyncio.create_task(self._drive(runner, stop_event))

                    self._stop_event = stop_event
                    self._task = task
                    return None

                if stop_event is not None:
                    stop_event.set()

            await self._wait_task_done(task)

    async def stop(self) -> None:
        """停止当前动画。"""
        async with self._lock:
            self._prune_finished_locked()
            stop_event, task = self._stop_event, self._task

            if task is None:
                return None

            if stop_event is not None:
                stop_event.set()

        await self._wait_task_done(task)

    async def _drive(self, runner: AnimRunner, stop_event: asyncio.Event) -> None:
        current_task = asyncio.current_task()

        try:
            await runner(stop_event)
        finally:
            async with self._lock:
                if self._task is current_task:
                    self._clear_locked()

    @staticmethod
    async def _wait_task_done(task: asyncio.Task[None] | None) -> None:
        if task is None:
            return None

        try:
            await asyncio.wait_for(asyncio.shield(task), timeout=1.0)
            return None
        except asyncio.TimeoutError:
            task.cancel()
        except asyncio.CancelledError:
            task.cancel()
            raise

        try:
            await task
        except asyncio.CancelledError:
            return None


if __name__ == '__main__':
    pass
