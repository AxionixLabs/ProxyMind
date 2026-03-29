# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
import asyncio
from loguru import logger
from mind_app.stream_support.render_coordinator import RenderCoord
from mind_app.stream_support.state_status import StatusFamily
from mind_app.stream_support.state_text import TextState
from mind_app.stream_support.output_record import StreamRecordWriter


class StreamUI(object):
    """流式终端 UI façade：统一封装 record、正文渲染与轻状态显示。"""

    STREAM = TextState.STREAM
    BLOCK  = TextState.BLOCK

    DEFAULT_REFRESH_PER_SECOND = 16

    def __init__(self, log_file: str) -> None:
        self.log_file = log_file

        self._has_stream_output = False
        self._pending_status_task: typing.Optional[asyncio.Task[None]] = None
        self.record_writer: StreamRecordWriter
        self._reset_components()

    async def open(self) -> None:
        await self.record_writer.open()

    async def stop(self) -> None:
        await self._cancel_pending_status_task()
        await self.coordinator.stop()
        await self.record_writer.close()
        self._reset_components()

    async def feed(
        self,
        chunk: typing.Optional[str],
        *,
        echo: bool = True,
        display: str = STREAM,
        display_chunk: typing.Optional[str] = None
    ) -> None:
        if not chunk:
            return None

        if display == self.STREAM:
            if not self._has_stream_output:
                self._has_stream_output = True
            await self.end_status()
            self.coordinator.release_status_slot()

        self.record_writer.write(chunk, block=(display == self.BLOCK))
        await self.coordinator.append(
            chunk,
            echo=echo,
            display=display,
            display_chunk=display_chunk
        )

    async def begin_builtin_status(
        self,
        text: typing.Optional[str],
        *,
        delay_sec: float = 0.35
    ) -> None:
        """显示 Responses builtin 名称，统一走 builtin 渲染族动画。"""
        if self._has_stream_output:
            return None
        await self._schedule_status_task(
            self._delayed_status(
                text,
                delay_sec=delay_sec,
                family="builtin",
                animated=True
            )
        )

    async def begin_tool_status(self) -> None:
        self.coordinator.hold_status_slot()
        text = "function calling"
        await self._schedule_status_task(
            self._delayed_tool_status(text)
        )

    async def begin_reply_wait_status(
        self,
        text: typing.Optional[str] = "thinking",
        *,
        delay_sec: float = 0.35
    ) -> None:
        self.coordinator.hold_status_slot()
        await self._schedule_status_task(
            self._delayed_status(
                text,
                delay_sec=delay_sec,
                family="wait",
                animated=True
            )
        )

    async def end_status(self) -> None:
        await self._cancel_pending_status_task()
        await self.coordinator.clear_status()

    async def settle_stream(self) -> None:
        await self.coordinator.settle_stream()

    def flush(self) -> None:
        self.record_writer.flush()

    def _reset_components(self) -> None:
        self._has_stream_output = False
        self._pending_status_task = None
        self.record_writer = StreamRecordWriter(self.log_file)
        self.coordinator = RenderCoord(
            refresh_per_second=self.DEFAULT_REFRESH_PER_SECOND
        )

    async def _schedule_status_task(
        self,
        coro: typing.Coroutine[typing.Any, typing.Any, None]
    ) -> None:
        await self._cancel_pending_status_task()
        task = asyncio.create_task(coro)
        task.add_done_callback(self._on_status_task_done)
        self._pending_status_task = task

    async def _cancel_pending_status_task(self) -> None:
        task = self._pending_status_task
        if not task:
            return None

        self._pending_status_task = None
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    def _clear_pending_status_task_ref(self, task: asyncio.Task[None]) -> None:
        if self._pending_status_task is task:
            self._pending_status_task = None

    @staticmethod
    def _on_status_task_done(task: asyncio.Task[None]) -> None:
        if task.cancelled():
            return None
        try:
            task.result()
        except asyncio.CancelledError:
            return None
        except Exception as e:
            logger.debug(f"[StreamUI] status task failed: {type(e).__name__}: {e}")

    async def _delayed_status(
        self,
        text: typing.Optional[str],
        *,
        delay_sec: float,
        family: StatusFamily,
        animated: bool
    ) -> None:
        task = asyncio.current_task()
        if task is None:
            return None
        try:
            await asyncio.sleep(delay_sec)
            self._clear_pending_status_task_ref(task)
            await self.coordinator.set_status(text, family=family, animated=animated)
        except asyncio.CancelledError:
            return None

    async def _delayed_tool_status(self, text: typing.Optional[str]) -> None:
        task = asyncio.current_task()
        if task is None:
            return None
        static_delay_sec = 0.25
        animate_delay_sec = 1.20
        try:
            await asyncio.sleep(static_delay_sec)
            await self.coordinator.set_status(text, family="tool", animated=False)

            animate_delay = animate_delay_sec - static_delay_sec
            if animate_delay > 0:
                await asyncio.sleep(animate_delay)
                await self.coordinator.set_status(text, family="tool", animated=True)

            self._clear_pending_status_task_ref(task)
        except asyncio.CancelledError:
            return None


if __name__ == '__main__':
    pass
