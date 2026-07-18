# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import asyncio
from collections.abc import (
    Awaitable,
    Callable
)
from dataclasses import dataclass
from .events import AppEvent

AppEventHandler = Callable[[AppEvent], Awaitable[None]]


@dataclass(slots=True)
class _QueuedEvent:
    event: AppEvent
    completion: asyncio.Future[None]


class AppEventDispatcher:
    """串行调度后台事件并等待界面完成投影。"""

    def __init__(self, handler: AppEventHandler) -> None:
        """初始化事件处理器和队列。"""
        self._handler = handler
        self._queue: asyncio.Queue[_QueuedEvent] = asyncio.Queue()
        self._worker: asyncio.Task[None] | None = None

    @property
    def running(self) -> bool:
        """返回调度循环是否正在运行。"""
        return self._worker is not None and not self._worker.done()

    @property
    def pending_count(self) -> int:
        """返回等待投影的事件数量。"""
        return self._queue.qsize()

    def start(self) -> None:
        """启动单一事件调度循环。"""
        if self.running:
            return
        self._worker = asyncio.create_task(self._run())

    async def dispatch(self, event: AppEvent) -> None:
        """提交事件并等待其处理完成。"""
        if not self.running:
            raise RuntimeError("AppEventDispatcher is not running")
        completion = asyncio.get_running_loop().create_future()
        await self._queue.put(_QueuedEvent(event, completion))
        await completion

    async def stop(self) -> None:
        """停止调度循环并取消未处理事件。"""
        worker = self._worker
        self._worker = None
        if worker is not None and not worker.done():
            worker.cancel()
        if worker is not None:
            await asyncio.gather(worker, return_exceptions=True)

        while not self._queue.empty():
            queued = self._queue.get_nowait()
            if not queued.completion.done():
                queued.completion.cancel()
            self._queue.task_done()

    async def _run(self) -> None:
        """按提交顺序处理事件。"""
        while True:
            queued = await self._queue.get()
            try:
                await self._handler(queued.event)
            except asyncio.CancelledError:
                if not queued.completion.done():
                    queued.completion.cancel()
                raise
            except Exception as exc:
                if not queued.completion.done():
                    queued.completion.set_exception(exc)
            else:
                if not queued.completion.done():
                    queued.completion.set_result(None)
            finally:
                self._queue.task_done()


if __name__ == '__main__':
    pass
