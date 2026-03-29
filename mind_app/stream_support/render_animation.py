# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
import asyncio


class AnimDriver(object):
    """通用动画节拍驱动，只负责相位推进和 tick 回调。"""

    def __init__(
        self,
        *,
        is_active: typing.Callable[[], bool],
        get_interval: typing.Callable[[], float],
        get_phase_rate: typing.Callable[[], float],
        on_tick: typing.Callable[[float], typing.Awaitable[None]]
    ) -> None:

        self.is_active       = is_active
        self.get_interval    = get_interval
        self.get_phase_rate  = get_phase_rate
        self.on_tick         = on_tick
        self.started_at: float = 0.0

        self.task: typing.Optional[asyncio.Task] = None

    async def start(self, *, reset_phase: bool = False) -> None:
        loop = asyncio.get_running_loop()
        if reset_phase:
            self.started_at = loop.time()
        elif not self.started_at:
            self.started_at = loop.time()

        if self.task is not None and not self.task.done():
            return None

        self.task = asyncio.create_task(self._loop())

    async def stop(self, *, reset_phase: bool = True) -> None:
        if self.task:
            self.task.cancel()
            try:
                await self.task
            except asyncio.CancelledError:
                pass
            self.task = None

        if reset_phase:
            self.started_at = 0.0

    async def _loop(self) -> None:
        loop = asyncio.get_running_loop()
        try:
            while self.is_active():
                await asyncio.sleep(self.get_interval())
                if not self.is_active():
                    break
                if not self.started_at:
                    self.started_at = loop.time()
                phase = (loop.time() - self.started_at) * self.get_phase_rate()
                await self.on_tick(phase)
        except asyncio.CancelledError:
            return None

if __name__ == '__main__':
    pass
