# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import json
import time
import typing
import asyncio
from loguru import logger
from rich.text import Text
from mind_core.design import Design
from mind_app.output.contracts import (
    BLOCK_OUTPUT,
    STREAM_OUTPUT,
    OutputDisplay,
    OutputPort
)
from mind_app.stream_render.coordinator import RenderCoord
from mind_app.stream_state.boundary import OutputBoundaryState
from mind_app.stream_state.status import StatusFamily
from mind_app.stream_io.output_record import StreamRecordWriter
from mind_app.stream_sanitize import sanitize_value


class StreamUI(OutputPort):
    """统一管理流式记录、正文渲染和状态显示。"""

    BLOCK: OutputDisplay  = BLOCK_OUTPUT
    STREAM: OutputDisplay = STREAM_OUTPUT

    def __init__(self, log_file: str, *, animate: bool = True) -> None:
        """初始化流式终端 UI 的记录、渲染和状态组件。"""
        self.log_file = log_file
        self.animate  = bool(animate)

        self._pending_status_task: typing.Optional[asyncio.Task[None]] = None
        self._pending_status_revealed: typing.Optional[asyncio.Event]  = None
        self._pending_status_force_reveal: bool                        = False


        self._active_status_visible_at: typing.Optional[float] = None
        self._active_status_min_visible_sec: float             = 0.0

        self._stream_output_fact: bool      = False
        self._stream_boundary_pending: bool = False

        self._reset_components()

        self.record_writer: StreamRecordWriter

    async def open(self) -> None:
        """打开流式输出记录。"""
        await self.record_writer.open()

    async def stop(self, *, blink: bool = True) -> None:
        """停止渲染与后台状态任务，并关闭记录。"""
        await self._cancel_pending_status_task()
        await self.coordinator.stop(blink=blink)
        await self.record_writer.close()
        self._reset_components()

    async def feed(
        self,
        chunk: typing.Optional[str],
        *,
        echo: bool = True,
        display: OutputDisplay = STREAM_OUTPUT,
        display_chunk: typing.Optional[str] = None,
        display_style: typing.Optional[str] = None,
        display_parts: typing.Optional[list[dict[str, typing.Optional[str]]]] = None,
        preserve_display_parts: bool = False
    ) -> None:
        """追加一段流式或块状文本到终端和记录。"""
        if not chunk:
            return None

        text = str(chunk)

        boundary_prefix = self._consume_stream_boundary_prefix(incoming_text=text)
        if boundary_prefix and display == self.STREAM:
            text = f"{boundary_prefix}{text}"

        raw_chunk = None
        if boundary_prefix and display == self.STREAM:
            raw_chunk = text

        if display == self.STREAM:
            if not self._stream_output_fact:
                self._stream_output_fact = True
            await self.end_status(immediate=True)
            self.coordinator.release_status_slot()

        self.record_writer.write(text, block=(display == self.BLOCK))

        await self.coordinator.append(
            text,
            echo=echo,
            display=display,
            display_chunk=display_chunk,
            raw_chunk=raw_chunk,
            display_style=display_style,
            display_parts=display_parts,
            preserve_display_parts=preserve_display_parts
        )

    async def prepare_external_output(self) -> None:
        """落版当前 live 文本，并在外部 UI 输出前消费 text.done 边界。"""
        display_text    = self.coordinator.text_state.display_text
        has_live_state  = bool(display_text)
        has_live_text   = bool(display_text.strip())
        boundary_prefix = self._consume_stream_boundary_prefix()

        if has_live_state:
            await self.settle_stream()
            await self.commit_live()

            boundary_prefix = self._external_output_boundary_prefix(
                boundary_prefix,
                has_live_text=has_live_text
            )
        if not boundary_prefix and not has_live_text:
            boundary_prefix = self._external_boundary_prefix_from_text_state()
        if not boundary_prefix:
            return None

        await self._print_boundary_prefix(boundary_prefix)
        self.coordinator.text_state.remember_external_spacing(
            display=self.BLOCK,
            text=boundary_prefix
        )

    async def begin_tool_status(self) -> None:
        """启动通用工具调用状态。"""
        await self.begin_custom_tool_status("function calling")

    async def begin_custom_tool_status(self, text: typing.Optional[str]) -> None:
        """启动自定义工具状态显示。"""
        if not self.animate:
            return None
        self.coordinator.hold_status_slot()

        await self._schedule_status_task(
            self._delayed_status_flow(
                text,
                show_delay_sec=0.18,
                animate_after_sec=0.18,
                family="tool",
                initial_animated=True
            )
        )

    async def begin_reply_wait_status(
        self,
        text: typing.Optional[str] = "thinking",
        *,
        delay_sec: float = 0.28,
        animate_after_sec: float | None = None
    ) -> None:
        """启动等待模型回复的状态显示。"""
        if not self.animate:
            return None
        animate_after = delay_sec if animate_after_sec is None else max(0.0, float(animate_after_sec))

        await self._schedule_status_task(
            self._delayed_status_flow(
                text,
                show_delay_sec=delay_sec,
                animate_after_sec=animate_after,
                family="wait"
            )
        )

    async def end_status(self, *, immediate: bool = False) -> None:
        """结束当前状态显示并清理相关后台任务。"""
        if not immediate:
            await self._wait_status_visibility_if_needed()
        await self._cancel_pending_status_task()
        self.coordinator.release_status_slot()
        await self.coordinator.clear_status(immediate=immediate)
        self._active_status_visible_at = None
        self._active_status_min_visible_sec = 0.0

    async def settle_stream(self) -> None:
        """同步当前流式正文到稳定显示状态。"""
        await self.coordinator.settle_stream()

    async def commit_live(self) -> None:
        """将当前 live 正文落版为普通终端输出。"""
        renderable = (
            self.coordinator.text_state.final_renderable()
            if self.coordinator.text_state.display_text else None
        )
        await self.coordinator.text_renderer.suspend(clear=True)
        if renderable is not None:
            self._print_direct(renderable)
            self.coordinator.text_state.clear()

    async def print_block(
        self,
        chunk: typing.Optional[str],
        *,
        display_parts: typing.Optional[list[dict[str, typing.Optional[str]]]] = None
    ) -> None:
        """直接打印块文本，不启动动态渲染器。"""
        if not chunk:
            return None

        text = str(chunk)

        boundary_prefix        = self._consume_stream_boundary_prefix(incoming_text=text)
        record_boundary_prefix = boundary_prefix

        if not boundary_prefix:
            boundary_prefix = self.coordinator.text_state.segment_prefix_for(
                display=self.BLOCK,
                incoming_text=text
            )
        if boundary_prefix:
            if record_boundary_prefix:
                self.record_writer.write_raw(record_boundary_prefix)
        self.record_writer.write(text, block=True)
        await self.coordinator.text_renderer.suspend(clear=True)

        await self._print_boundary_prefix(boundary_prefix, record=False)

        self._print_direct(
            self._parts_renderable(display_parts)
            if display_parts is not None
            else Text(text.rstrip("\n"), style="bold")
        )
        self.coordinator.text_state.remember_external_output(
            display=self.BLOCK,
            text=text if text.endswith("\n") else f"{text}\n"
        )

    def flush(self) -> None:
        """刷新输出记录缓冲区。"""
        self.record_writer.flush()

    def mark_stream_boundary(self) -> None:
        """标记下一段输出前需要处理流式边界。"""
        self._stream_boundary_pending = True

    async def record_hidden_output(self, text: str) -> None:
        """记录不直接展示的块输出。"""
        if not text:
            return None

        value = str(text)
        self._consume_stream_boundary_prefix(incoming_text=value)
        self.record_writer.write(value, block=True)

    def record_tool_arguments(
        self,
        name: str,
        arguments: dict[str, typing.Any],
        *,
        call_id: typing.Optional[str] = None
    ) -> None:
        """记录工具调用参数的审计信息。"""
        payload   = self._tool_arguments_audit_payload(arguments)
        call_part = f" call_id={call_id}" if call_id else ""

        self.record_writer.write_audit(
            f"# tool_args tool={name}{call_part} arguments={payload}"
        )

    @staticmethod
    def _print_direct(renderable: typing.Any) -> None:
        """直接向终端打印一个可渲染对象。"""
        Design.console.print(renderable)

    @staticmethod
    def _external_output_boundary_prefix(prefix: str, *, has_live_text: bool) -> str:
        """在模型正文和外部交互 UI 之间保留一个视觉空行。"""
        if not has_live_text:
            return prefix
        return "\n"

    def _external_boundary_prefix_from_text_state(self) -> str:
        """根据上一段直接输出，为外部交互 UI 补足视觉空行。"""
        boundary = self.coordinator.text_state.external_boundary
        if not boundary or not boundary.get("has_text"):
            return ""

        trailing = max(0, int(boundary.get("trailing_newlines") or 0))
        if trailing >= 2:
            return ""

        return "\n" * (2 - trailing)

    @staticmethod
    def _print_raw(text: str) -> None:
        """直接向终端写入原始文本。"""
        Design.console.print("", end=text)

    @staticmethod
    def _parts_renderable(parts: list[dict[str, typing.Optional[str]]]) -> Text:
        """把带样式片段转换为终端文本对象。"""
        renderable = Text()
        for part in parts:
            part_text = str(part.get("text") or "")
            if part_text:
                renderable.append(part_text, style=part.get("style") or "bold")
        renderable.rstrip()
        return renderable

    @staticmethod
    def _on_status_task_done(task: asyncio.Task[None]) -> None:
        """处理状态后台任务结束后的异常记录。"""
        if task.cancelled():
            return None
        try:
            task.result()
        except asyncio.CancelledError:
            return None
        except Exception as e:
            logger.debug(f"[StreamUI] status task failed: {type(e).__name__}: {e}")

    @staticmethod
    def _tool_arguments_audit_payload(arguments: dict[str, typing.Any]) -> str:
        """生成工具参数审计记录的 JSON 文本。"""
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

    def _reset_components(self) -> None:
        """重置渲染协调器、记录器和运行期状态。"""
        self._stream_output_fact            = False
        self._stream_boundary_pending       = False
        self._pending_status_task           = None
        self._pending_status_force_reveal   = False
        self._pending_status_revealed       = None
        self._active_status_visible_at      = None
        self._active_status_min_visible_sec = 0.0

        refresh_per_second = 16

        self.record_writer = StreamRecordWriter(self.log_file)
        self.coordinator = RenderCoord(
            refresh_per_second=refresh_per_second
        )

    def _clear_pending_status_task_ref(self, task: asyncio.Task[None]) -> None:
        """在指定状态任务结束后清理任务引用。"""
        if self._pending_status_task is task:
            self._pending_status_task         = None
            self._pending_status_force_reveal = False
            self._pending_status_revealed     = None

    def _mark_status_visible(self, min_visible_sec: float) -> None:
        """记录状态开始可见的时间和最短显示时长。"""
        self._active_status_visible_at = time.perf_counter()
        self._active_status_min_visible_sec = max(0.0, float(min_visible_sec))

    def _consume_stream_boundary_prefix(self, *, incoming_text: str | None = None) -> str:
        """消费待处理的流式边界，并返回需要补充的前缀。"""
        if not self._stream_boundary_pending:
            return ""

        self._stream_boundary_pending = False

        if incoming_text and incoming_text.startswith("\n"):
            return ""

        source = self.coordinator.text_state.display_text
        if not source:
            return ""

        trailing = OutputBoundaryState.count_trailing_newlines(source)
        needed   = max(0, 1 - trailing)

        return "\n" * needed

    async def _print_boundary_prefix(self, prefix: str, *, record: bool = True) -> None:
        """打印边界前缀，并按需写入记录。"""
        if not prefix:
            return None
        if record:
            self.record_writer.write_raw(prefix)
        await self.coordinator.text_renderer.suspend(clear=True)
        self._print_raw(prefix)

    async def _wait_status_visibility_if_needed(self) -> None:
        """在结束状态前等待必要的最短可见时间。"""
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

    async def _cancel_pending_status_task(self) -> None:
        """取消等待中的状态显示任务。"""
        task = self._pending_status_task
        if not task:
            return None

        self._pending_status_task         = None
        self._pending_status_force_reveal = False
        self._pending_status_revealed     = None

        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    async def _schedule_status_task(
        self,
        coro: typing.Coroutine[typing.Any, typing.Any, None],
        *,
        force_reveal: bool = False
    ) -> None:
        """注册新的状态显示后台任务。"""
        await self._cancel_pending_status_task()
        task = asyncio.create_task(coro)
        task.add_done_callback(self._on_status_task_done)
        self._pending_status_task = task
        self._pending_status_force_reveal = force_reveal
        self._pending_status_revealed = asyncio.Event() if force_reveal else None

    async def _delayed_status_flow(
        self,
        text: typing.Optional[str],
        *,
        show_delay_sec: float,
        animate_after_sec: float,
        family: StatusFamily,
        min_visible_sec: float = 0.0,
        initial_animated: bool = False
    ) -> None:
        """按延迟策略显示并启动状态动画。"""
        task = asyncio.current_task()
        if task is None:
            return None

        try:
            await asyncio.sleep(show_delay_sec)
            await self.coordinator.set_status(text, family=family, animated=initial_animated)

            self._mark_status_visible(min_visible_sec)
            if self._pending_status_revealed is not None:
                self._pending_status_revealed.set()

            animate_delay = animate_after_sec - show_delay_sec
            if initial_animated:
                pass
            elif animate_delay > 0:
                await asyncio.sleep(animate_delay)
                await self.coordinator.set_status(text, family=family, animated=True)
            elif animate_after_sec <= show_delay_sec:
                await self.coordinator.set_status(text, family=family, animated=True)

            self._clear_pending_status_task_ref(task)

        except asyncio.CancelledError:
            return None


if __name__ == '__main__':
    pass
