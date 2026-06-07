# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
import asyncio


class IdleStatusTimer(object):
    """在事件流短暂停顿后触发一个延迟状态。"""

    def __init__(
        self,
        callback: typing.Callable[[], typing.Awaitable[None]],
        *,
        delay_sec: float = 0.9
    ) -> None:
        self.callback = callback
        self.delay_sec = max(0.0, float(delay_sec))
        self.task: asyncio.Task[None] | None = None

    def reschedule(self) -> None:
        self.cancel_nowait()
        self.task = asyncio.create_task(self._run())

    def cancel_nowait(self) -> None:
        if self.task is not None:
            self.task.cancel()

    async def cancel(self) -> None:
        task = self.task
        if task is None:
            return None

        self.task = None
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    async def _run(self) -> None:
        task = asyncio.current_task()
        try:
            await asyncio.sleep(self.delay_sec)
            await self.callback()
        except asyncio.CancelledError:
            raise
        finally:
            if self.task is task:
                self.task = None


if __name__ == '__main__':
    pass
