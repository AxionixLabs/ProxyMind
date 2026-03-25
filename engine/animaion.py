# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
import asyncio


class AsyncAnimManager(object):
    """单实例异步动画管理器。"""

    def __init__(self) -> None:
        self._lock: asyncio.Lock = asyncio.Lock()
        self._event: typing.Optional[asyncio.Event] = None
        self._task: typing.Optional[asyncio.Task] = None

    @property
    def running(self) -> bool:
        """当前是否有动画在运行。"""
        task = self._task
        return bool(task and not task.done())

    async def start(
        self,
        runner: typing.Callable[[asyncio.Event], typing.Awaitable[None]]
    ) -> None:
        """启动动画；启动前会严格停止当前动画。"""
        while True:
            async with self._lock:
                event, task = self._event, self._task

                if task and task.done():
                    self._event = None
                    self._task = None
                    event, task = None, None

                if not task:
                    event = asyncio.Event()
                    task = asyncio.create_task(self._drive(runner, event))

                    self._event = event
                    self._task = task
                    return None

                if event:
                    event.set()

            await self._wait_task_done(task)

    async def stop(self) -> None:
        """停止当前动画。"""
        async with self._lock:
            event, task = self._event, self._task

            if task and task.done():
                self._event = None
                self._task = None
                return None

            if event:
                event.set()

        await self._wait_task_done(task)

    async def _drive(
        self,
        runner: typing.Callable[[asyncio.Event], typing.Awaitable[None]],
        event: asyncio.Event
    ) -> None:
        task = asyncio.current_task()

        try:
            await runner(event)
        finally:
            async with self._lock:
                if self._task is task:
                    self._event = None
                    self._task = None

    @staticmethod
    async def _wait_task_done(
        task: typing.Optional[asyncio.Task]
    ) -> None:
        if not task:
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
