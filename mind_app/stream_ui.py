# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import time
import typing
import asyncio
from loguru import logger
from mind_app.stream_render.coordinator import RenderCoord
from mind_app.stream_state.status import StatusFamily
from mind_app.stream_state.text import TextState
from mind_app.stream_io.output_record import StreamRecordWriter


class StreamUI(object):
    """流式终端 UI façade：统一封装 record、正文渲染与轻状态显示。"""

    STREAM = TextState.STREAM
    BLOCK  = TextState.BLOCK

    DEFAULT_REFRESH_PER_SECOND = 16

    def __init__(self, log_file: str) -> None:
        self.log_file = log_file

        self._has_stream_output = False
        self._pending_status_task: typing.Optional[asyncio.Task[None]] = None
        self._pending_status_force_reveal = False
        self._pending_status_revealed: typing.Optional[asyncio.Event] = None
        self._active_status_visible_at: typing.Optional[float] = None
        self._active_status_min_visible_sec = 0.0
        self.record_writer: StreamRecordWriter
        self._reset_components()

    async def open(self) -> None:
        await self.record_writer.open()

    async def stop(self, *, blink: bool = True) -> None:
        await self._cancel_pending_status_task()
        await self.coordinator.stop(blink=blink)
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
        delay_sec: float = 0.0
    ) -> None:
        """显示 Responses builtin 名称，立即露出并至少保留一段可见动画时间。"""
        if self._has_stream_output:
            return None
        await self._schedule_status_task(
            self._delayed_status_flow(
                text,
                show_delay_sec=delay_sec,
                animate_after_sec=0.0,
                family="builtin",
                min_visible_sec=0.85
            ),
            force_reveal=True
        )

    async def begin_tool_status(self) -> None:
        self.coordinator.hold_status_slot()
        text = "function calling"
        await self._schedule_status_task(
            self._delayed_status_flow(
                text,
                show_delay_sec=0.18,
                animate_after_sec=0.72,
                family="tool"
            )
        )

    async def begin_reply_wait_status(
        self,
        text: typing.Optional[str] = "thinking",
        *,
        delay_sec: float = 0.28
    ) -> None:
        self.coordinator.hold_status_slot()
        await self._schedule_status_task(
            self._delayed_status_flow(
                text,
                show_delay_sec=delay_sec,
                animate_after_sec=0.72,
                family="wait",
            )
        )

    async def end_status(self) -> None:
        await self._wait_status_visibility_if_needed()
        await self._cancel_pending_status_task()
        await self.coordinator.clear_status()
        self._active_status_visible_at = None
        self._active_status_min_visible_sec = 0.0

    async def settle_stream(self) -> None:
        await self.coordinator.settle_stream()

    def flush(self) -> None:
        self.record_writer.flush()

    def _reset_components(self) -> None:
        self._has_stream_output = False
        self._pending_status_task = None
        self._pending_status_force_reveal = False
        self._pending_status_revealed = None
        self._active_status_visible_at = None
        self._active_status_min_visible_sec = 0.0
        self.record_writer = StreamRecordWriter(self.log_file)
        self.coordinator = RenderCoord(
            refresh_per_second=self.DEFAULT_REFRESH_PER_SECOND
        )

    async def _schedule_status_task(
        self,
        coro: typing.Coroutine[typing.Any, typing.Any, None],
        *,
        force_reveal: bool = False
    ) -> None:
        await self._cancel_pending_status_task()
        task = asyncio.create_task(coro)
        task.add_done_callback(self._on_status_task_done)
        self._pending_status_task = task
        self._pending_status_force_reveal = force_reveal
        self._pending_status_revealed = asyncio.Event() if force_reveal else None

    async def _cancel_pending_status_task(self) -> None:
        task = self._pending_status_task
        if not task:
            return None

        self._pending_status_task = None
        self._pending_status_force_reveal = False
        self._pending_status_revealed = None
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    def _clear_pending_status_task_ref(self, task: asyncio.Task[None]) -> None:
        if self._pending_status_task is task:
            self._pending_status_task = None
            self._pending_status_force_reveal = False
            self._pending_status_revealed = None

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

    async def _delayed_status_flow(
        self,
        text: typing.Optional[str],
        *,
        show_delay_sec: float,
        animate_after_sec: float,
        family: StatusFamily,
        min_visible_sec: float = 0.0
    ) -> None:
        task = asyncio.current_task()
        if task is None:
            return None
        try:
            await asyncio.sleep(show_delay_sec)
            await self.coordinator.set_status(text, family=family, animated=False)
            self._mark_status_visible(min_visible_sec)
            if self._pending_status_revealed is not None:
                self._pending_status_revealed.set()
            animate_delay = animate_after_sec - show_delay_sec
            if animate_delay > 0:
                await asyncio.sleep(animate_delay)
                await self.coordinator.set_status(text, family=family, animated=True)
            elif animate_after_sec <= show_delay_sec:
                await self.coordinator.set_status(text, family=family, animated=True)

            self._clear_pending_status_task_ref(task)
        except asyncio.CancelledError:
            return None

    def _mark_status_visible(self, min_visible_sec: float) -> None:
        self._active_status_visible_at = time.perf_counter()
        self._active_status_min_visible_sec = max(0.0, float(min_visible_sec))

    async def _wait_status_visibility_if_needed(self) -> None:
        task = self._pending_status_task
        revealed = self._pending_status_revealed
        if task and self._pending_status_force_reveal and revealed is not None:
            try:
                await revealed.wait()
            except asyncio.CancelledError:
                return None

        if not self._active_status_visible_at or self._active_status_min_visible_sec <= 0:
            return None

        deadline = self._active_status_visible_at + self._active_status_min_visible_sec
        remaining = deadline - time.perf_counter()
        if remaining > 0:
            await asyncio.sleep(remaining)


if __name__ == '__main__':
    pass
