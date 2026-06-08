# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import json
import time
import typing
import asyncio
from loguru import logger
from rich.text import Text
from backend.utilities.trace import sanitize_value
from mind_core.design import Design
from mind_app.stream_render.coordinator import RenderCoord
from mind_app.stream_state.status import StatusFamily
from mind_app.stream_state.text import TextState
from mind_app.stream_io.output_record import StreamRecordWriter


class StreamUI(object):
    """流式终端 UI façade：统一封装 record、正文渲染与轻状态显示。"""

    BLOCK  = TextState.BLOCK
    STREAM = TextState.STREAM

    def __init__(self, log_file: str) -> None:
        self.log_file = log_file

        self._has_stream_output = False
        self._pending_status_task: typing.Optional[asyncio.Task[None]] = None
        self._pending_status_force_reveal = False
        self._pending_status_revealed: typing.Optional[asyncio.Event] = None
        self._active_status_visible_at: typing.Optional[float] = None
        self._active_status_min_visible_sec = 0.0
        self._heal_status_text: str = ""
        self._heal_status_pending_text: str = ""
        self._heal_status_last_flush_at: float = 0.0
        self._heal_status_flush_task: typing.Optional[asyncio.Task[None]] = None
        self._stream_boundary_pending = False
        self.record_writer: StreamRecordWriter
        self._reset_components()

    async def open(self) -> None:
        await self.record_writer.open()

    async def stop(self, *, blink: bool = True) -> None:
        await self._cancel_heal_status_flush_task()
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
        display_chunk: typing.Optional[str] = None,
        display_style: typing.Optional[str] = None,
        display_parts: typing.Optional[list[dict[str, typing.Optional[str]]]] = None
    ) -> None:
        if not chunk:
            return None

        text = str(chunk)
        if display == self.STREAM and self._stream_boundary_pending:
            self._stream_boundary_pending = False
            if self.coordinator.text_state.display_text and not text.startswith("\n"):
                text = f"\n{text}"

        if display == self.STREAM:
            if not self._has_stream_output:
                self._has_stream_output = True
            await self.end_status(immediate=True)
            self.coordinator.release_status_slot()

        self.record_writer.write(text, block=(display == self.BLOCK))
        await self.coordinator.append(
            text,
            echo=echo,
            display=display,
            display_chunk=display_chunk,
            display_style=display_style,
            display_parts=display_parts
        )

    def mark_stream_boundary(self) -> None:
        self._stream_boundary_pending = True

    def record_tool_arguments(
        self,
        name: str,
        arguments: dict[str, typing.Any],
        *,
        call_id: typing.Optional[str] = None
    ) -> None:
        payload   = self._tool_arguments_audit_payload(arguments)
        call_part = f" call_id={call_id}" if call_id else ""

        self.record_writer.write_audit(
            f"# tool_args tool={name}{call_part} arguments={payload}"
        )

    async def begin_builtin_status(
        self,
        text: typing.Optional[str],
        *,
        delay_sec: float = 0.12
    ) -> None:
        """显示 Responses builtin 名称，短延迟后露出，避免极短 builtin 闪屏。"""
        if self._has_stream_output:
            return None
        status_text, family = self._compose_builtin_status(text)
        await self._schedule_status_task(
            self._delayed_status_flow(
                status_text,
                show_delay_sec=delay_sec,
                animate_after_sec=delay_sec,
                family=family,
                min_visible_sec=0.0
            )
        )

    async def begin_tool_status(self) -> None:
        await self.begin_custom_tool_status("function calling")

    async def begin_custom_tool_status(self, text: typing.Optional[str]) -> None:
        self.coordinator.hold_status_slot()
        await self._schedule_status_task(
            self._delayed_status_flow(
                text,
                show_delay_sec=0.18,
                animate_after_sec=0.72,
                family="tool"
            )
        )

    async def begin_code_status(
        self,
        text: str,
        *,
        delay_sec: float = 0.18,
        min_visible_sec: float = 0.32
    ) -> None:
        self.coordinator.hold_status_slot()
        if delay_sec <= 0:
            await self._cancel_pending_status_task()
            await self.coordinator.set_status(text, family="code", animated=True)
            self._mark_status_visible(min_visible_sec)
            return None

        await self._schedule_status_task(
            self._delayed_status_flow(
                text,
                show_delay_sec=delay_sec,
                animate_after_sec=delay_sec,
                family="code",
                min_visible_sec=min_visible_sec
            )
        )

    async def begin_reply_wait_status(
        self,
        text: typing.Optional[str] = "thinking",
        *,
        delay_sec: float = 0.28
    ) -> None:
        await self._schedule_status_task(
            self._delayed_status_flow(
                text,
                show_delay_sec=delay_sec,
                animate_after_sec=delay_sec,
                family="wait",
            )
        )

    async def begin_heal_status(
        self,
        summary: typing.Optional[str] = None,
        *,
        delay_sec: float = 0.0
    ) -> None:
        self.coordinator.hold_status_slot()
        await self._cancel_pending_status_task()
        await self._cancel_heal_status_flush_task()
        text = self._compose_heal_status_text(summary)
        self._heal_status_text = text
        self._heal_status_pending_text = ""
        self._heal_status_last_flush_at = time.perf_counter()
        if delay_sec > 0:
            await self._schedule_status_task(
                self._delayed_status_flow(
                    text,
                    show_delay_sec=delay_sec,
                    animate_after_sec=delay_sec,
                    family="heal",
                )
            )
            return None

        await self.coordinator.set_status(text, family="heal", animated=True)
        self._mark_status_visible(0.0)

    async def begin_loop_status(
        self,
        summary: typing.Optional[str] = None
    ) -> None:
        self.coordinator.hold_status_slot()
        await self._cancel_pending_status_task()
        text = self._compose_loop_status_text(summary)
        await self.coordinator.set_status(text, family="loop", animated=True)
        self._mark_status_visible(0.0)

    async def update_loop_status_summary(
        self,
        summary: typing.Optional[str]
    ) -> None:
        self.coordinator.hold_status_slot()
        await self._cancel_pending_status_task()
        await self.coordinator.set_status(
            self._compose_loop_status_text(summary),
            family="loop",
            animated=True,
            reset_phase_on_text_change=False
        )
        self._mark_status_visible(0.0)

    async def update_heal_status_summary(
        self,
        summary: typing.Optional[str]
    ) -> None:
        text = self._compose_heal_status_text(summary)
        if not text or text == self._heal_status_text:
            return None

        throttle  = 0.28
        now       = time.perf_counter()
        remaining = throttle - (now - self._heal_status_last_flush_at)

        self._heal_status_pending_text = text

        if remaining <= 0:
            await self._flush_heal_status_text(text)
            return None

        if self._heal_status_flush_task is None:
            self._heal_status_flush_task = asyncio.create_task(
                self._flush_heal_status_after(remaining)
            )

    async def end_status(self, *, immediate: bool = False) -> None:
        if not immediate:
            await self._wait_status_visibility_if_needed()
        await self._cancel_heal_status_flush_task()
        await self._cancel_pending_status_task()
        self.coordinator.release_status_slot()
        await self.coordinator.clear_status(immediate=immediate)
        self._active_status_visible_at = None
        self._active_status_min_visible_sec = 0.0
        self._reset_heal_status_state()

    async def settle_stream(self) -> None:
        await self.coordinator.settle_stream()

    async def commit_live(self) -> None:
        renderable = (
            self.coordinator.text_state.final_renderable()
            if self.coordinator.text_state.display_text else None
        )
        await self.coordinator.text_renderer.suspend()
        if renderable is not None:
            self._print_direct(renderable)
            self.coordinator.text_state.clear()

    async def print_block(
        self,
        chunk: typing.Optional[str],
        *,
        display_parts: typing.Optional[list[dict[str, typing.Optional[str]]]] = None
    ) -> None:
        """直接打印块文本，不启动 live renderer。"""
        if not chunk:
            return None

        text = str(chunk)
        self.record_writer.write(text, block=True)
        await self.coordinator.text_renderer.suspend()
        self._print_direct(
            self._parts_renderable(display_parts)
            if display_parts is not None
            else Text(text.rstrip("\n"), style="bold")
        )

    def flush(self) -> None:
        self.record_writer.flush()

    def _reset_components(self) -> None:
        self._has_stream_output = False
        self._stream_boundary_pending = False
        self._pending_status_task = None
        self._pending_status_force_reveal = False
        self._pending_status_revealed = None
        self._active_status_visible_at = None
        self._active_status_min_visible_sec = 0.0
        self._reset_heal_status_state()

        refresh_per_second = 16

        self.record_writer = StreamRecordWriter(self.log_file)
        self.coordinator = RenderCoord(
            refresh_per_second=refresh_per_second
        )

    def _clear_pending_status_task_ref(self, task: asyncio.Task[None]) -> None:
        if self._pending_status_task is task:
            self._pending_status_task = None
            self._pending_status_force_reveal = False
            self._pending_status_revealed = None

    def _mark_status_visible(self, min_visible_sec: float) -> None:
        self._active_status_visible_at = time.perf_counter()
        self._active_status_min_visible_sec = max(0.0, float(min_visible_sec))

    def _reset_heal_status_state(self) -> None:
        self._heal_status_text = ""
        self._heal_status_pending_text = ""
        self._heal_status_last_flush_at = 0.0
        self._heal_status_flush_task = None

    @staticmethod
    def _print_direct(renderable: typing.Any) -> None:
        Design.console.print(renderable)

    @staticmethod
    def _parts_renderable(
        parts: list[dict[str, typing.Optional[str]]]
    ) -> Text:
        renderable = Text()
        for part in parts:
            part_text = str(part.get("text") or "")
            if part_text:
                renderable.append(part_text, style=part.get("style") or "bold")
        renderable.rstrip()
        return renderable

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

    @classmethod
    def _compose_heal_status_text(cls, summary: typing.Optional[str]) -> str:
        base_title = "restoring signal"
        normalized = " ".join(str(summary or "").split())
        if not normalized:
            return base_title
        lower = normalized.lower()
        base_lower = base_title.lower()
        if lower.startswith(f"{base_lower} · "):
            return normalized
        if lower == base_lower:
            return base_title
        return f"{base_title} · {normalized}"

    @staticmethod
    def _tool_arguments_audit_payload(arguments: dict[str, typing.Any]) -> str:
        try:
            sanitized = sanitize_value(arguments, max_depth=6, max_items=50)
            return json.dumps(
                sanitized,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                default=str
            )
        except Exception as exc:
            return json.dumps(
                {"error": f"{type(exc).__name__}: {exc}"},
                ensure_ascii=False,
                separators=(",", ":")
            )

    @classmethod
    def _compose_loop_status_text(cls, summary: typing.Optional[str]) -> str:
        base_title = "loop steps"
        normalized = " ".join(str(summary or "").split())
        if not normalized:
            return base_title
        lower = normalized.lower()
        base_lower = base_title.lower()
        if lower.startswith(f"{base_lower} · "):
            return normalized
        if lower == base_lower:
            return base_title
        return f"{base_title} · {normalized}"

    @classmethod
    def _compose_builtin_status(
        cls,
        text: typing.Optional[str]
    ) -> tuple[typing.Optional[str], StatusFamily]:
        normalized = " ".join(str(text or "").split())
        if normalized.lower() in {"chat", "fast", "plan", "xtra"}:
            return Design.mode_status_text(normalized), "mode"
        return text, "builtin"

    async def _cancel_heal_status_flush_task(self) -> None:
        task = self._heal_status_flush_task
        if task is None:
            return None
        self._heal_status_flush_task = None
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            return None

    async def _flush_heal_status_after(self, delay_sec: float) -> None:
        try:
            await asyncio.sleep(max(0.0, float(delay_sec)))
            if self._heal_status_pending_text:
                await self._flush_heal_status_text(self._heal_status_pending_text)
        except asyncio.CancelledError:
            return None
        finally:
            self._heal_status_flush_task = None

    async def _flush_heal_status_text(self, text: str) -> None:
        self._heal_status_pending_text = ""
        self._heal_status_text = text
        self._heal_status_last_flush_at = time.perf_counter()
        await self.coordinator.set_status(
            text,
            family="heal",
            animated=True,
            reset_phase_on_text_change=False
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

    async def _wait_status_visibility_if_needed(self) -> None:
        task     = self._pending_status_task
        revealed = self._pending_status_revealed

        if task and self._pending_status_force_reveal and revealed is not None:
            try:
                await revealed.wait()
            except asyncio.CancelledError:
                return None

        if not self._active_status_visible_at or self._active_status_min_visible_sec <= 0:
            return None

        deadline  = self._active_status_visible_at + self._active_status_min_visible_sec
        remaining = deadline - time.perf_counter()

        if remaining > 0:
            await asyncio.sleep(remaining)


if __name__ == '__main__':
    pass
