# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import json
import random
import typing
import asyncio
from rich.text import Text
from mind_app.output.contracts import (
    BLOCK_OUTPUT,
    STREAM_OUTPUT,
    OutputDisplay,
    OutputPort
)
from mind_app.stream_io.output_record import StreamRecordWriter
from mind_app.stream_render.animation import AnimDriver
from mind_app.stream_sanitize import sanitize_value
from mind_app.stream_state.status import (
    StatusFamily,
    StatusState
)
from mind_app.stream_state.text import TextState
from mind_core.design import Design
from mind_core.design.utils import TYPEWRITER_CURSOR_STYLE
from ..core.runtime import TuiRuntime


class TuiOutputControl(OutputPort):
    """把单轮流式内容和状态写入持久 TUI。"""

    BLOCK: OutputDisplay = BLOCK_OUTPUT
    STREAM: OutputDisplay = STREAM_OUTPUT

    def __init__(
        self,
        log_file: str,
        *,
        runtime: TuiRuntime,
        animate: bool = True,
    ) -> None:
        self.log_file = log_file
        self.runtime = runtime
        self.animate = bool(animate)

        self.text_state = TextState(width_provider=lambda: self.terminal_width)
        self.record_writer = StreamRecordWriter(log_file)
        self.status_state = StatusState()
        self.status_driver = AnimDriver(
            is_active=lambda: self.status_state.animating,
            get_interval=self.status_state.interval,
            get_phase_rate=self.status_state.phase_rate,
            on_tick=self._on_status_tick,
        )
        self._pending_status_task: asyncio.Task[None] | None = None
        self._stream_boundary_pending = False
        self._cursor = random.choice(("█", "▉", "▋"))

    @property
    def terminal_width(self) -> int | None:
        """返回当前 TUI 宽度。"""
        return self.runtime.terminal_width

    @property
    def terminal_height(self) -> int | None:
        """返回当前 TUI 高度。"""
        return self.runtime.terminal_height

    async def open(self) -> None:
        """打开当前输出记录。"""
        await self.record_writer.open()

    async def stop(self, *, blink: bool = True) -> None:
        """停止状态动画、提交当前内容并关闭记录。"""
        _ = blink
        await self.end_status(immediate=True)
        self._commit_current()
        await self.record_writer.close()

    async def feed(
        self,
        chunk: typing.Optional[str],
        *,
        echo: bool = True,
        display: OutputDisplay = STREAM_OUTPUT,
        display_chunk: typing.Optional[str] = None,
        display_style: typing.Optional[str] = None,
        display_parts: typing.Optional[list[dict[str, typing.Optional[str]]]] = None,
        preserve_display_parts: bool = False,
    ) -> None:
        """向当前 TUI 内容块追加一段流式或块状文本。"""
        if not chunk:
            return None

        text = str(chunk)
        stream_boundary = self._stream_boundary_pending and display == self.STREAM
        if stream_boundary:
            self._stream_boundary_pending = False
            if not self.text_state.display_text:
                self.runtime.append_gap()
            if (
                self.record_writer.last_display == self.STREAM
                and self.record_writer.trailing_newlines < 1
                and not text.startswith("\n")
            ):
                self.record_writer.write_raw("\n")

        self.record_writer.write(text, block=(display == self.BLOCK))
        if display == self.STREAM:
            await self.end_status(immediate=True)

        typewriter = bool(
            self.animate
            and echo
            and display == self.STREAM
            and display_chunk is None
            and display_style is None
            and display_parts is None
        )
        if typewriter:
            await self._append_typewriter(text)
            return None

        self.text_state.append(
            text,
            echo=echo,
            display=display,
            display_chunk=display_chunk,
            display_style=display_style,
            display_parts=display_parts,
            preserve_display_parts=preserve_display_parts,
        )
        if echo:
            self._render_active(cursor=False)

    async def prepare_external_output(self) -> None:
        """在外部展示前提交当前流式内容。"""
        if self._commit_current():
            self.runtime.append_gap()

    async def begin_tool_status(self) -> None:
        """启动通用工具状态。"""
        await self.begin_custom_tool_status("function calling")

    async def begin_custom_tool_status(self, text: typing.Optional[str]) -> None:
        """启动工具状态动画。"""
        await self._schedule_status(text, family="tool", delay_sec=0.18)

    async def begin_reply_wait_status(
        self,
        text: typing.Optional[str] = "thinking",
        *,
        delay_sec: float = 0.28,
        animate_after_sec: float | None = None,
    ) -> None:
        """启动等待回复状态动画。"""
        _ = animate_after_sec
        await self._schedule_status(text, family="wait", delay_sec=delay_sec)

    async def end_status(self, *, immediate: bool = False) -> None:
        """结束当前输出状态动画。"""
        _ = immediate
        task = self._pending_status_task
        self._pending_status_task = None
        if task is not None:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        self.status_state.reset()
        await self.status_driver.stop(reset_phase=True)
        self.runtime.clear_status_renderable()

    async def settle_stream(self) -> None:
        """立即同步当前流式内容。"""
        if self.text_state.display_text:
            self._render_active(cursor=False)

    async def record_hidden_output(self, text: str) -> None:
        """记录不直接展示的块状内容。"""
        if text:
            self.record_writer.write(str(text), block=True)

    def mark_stream_boundary(self) -> None:
        """标记下一段流式内容边界。"""
        self._commit_current()
        self.runtime.append_gap()
        self._stream_boundary_pending = True

    def record_tool_arguments(
        self,
        name: str,
        arguments: dict[str, typing.Any],
        *,
        call_id: typing.Optional[str] = None,
    ) -> None:
        """记录工具调用参数审计信息。"""
        call_part = f" call_id={call_id}" if call_id else ""
        self.record_writer.write_audit(
            f"# tool_args tool={name}{call_part} arguments={self._audit_payload(arguments)}"
        )

    async def print_block(
        self,
        chunk: typing.Optional[str],
        *,
        display_parts: typing.Optional[list[dict[str, typing.Optional[str]]]] = None,
    ) -> None:
        """提交当前内容后追加一个直接展示块。"""
        if not chunk:
            return None
        self._commit_current()

        text = str(chunk)
        self.record_writer.write(text, block=True)
        if display_parts is None:
            renderable = Text(text.rstrip("\n"), style="bold")
        else:
            renderable = Text()
            for part in display_parts:
                value = str(part.get("text") or "")
                if value:
                    renderable.append(value, style=part.get("style") or "bold")
            renderable.rstrip()
        self.runtime.append_block(renderable)

    def flush(self) -> None:
        """刷新当前输出记录。"""
        self.record_writer.flush()

    def _commit_current(self) -> bool:
        """把当前动态内容提交为稳定 TUI 内容块并返回提交状态。"""
        if not self.text_state.display_text:
            self.runtime.clear_active_renderable()
            return False
        self.runtime.commit_active_renderable(self.text_state.final_renderable())
        self.text_state.clear()
        return True

    async def _append_typewriter(self, text: str) -> None:
        """按现有打字机节奏分批展示流式文本。"""
        size = max(1, len(text))
        for index in range(0, len(text), 2):
            delta = text[index:index + 2]
            self.text_state.append(delta, display=self.STREAM)
            self._render_active(cursor=True)

            progress = index / max(1, size - 1)
            delay = 0.010 + (0.0065 - 0.010) * progress
            if any(char in "。.!！?？" for char in delta):
                delay += 0.035
            elif any(char in "；;：:" for char in delta):
                delay += 0.025
            elif any(char in "，," for char in delta):
                delay += 0.015
            await asyncio.sleep(max(0.0015, delay))

    def _render_active(self, *, cursor: bool) -> None:
        """刷新当前流式内容并按需附加打字机光标。"""
        renderable = self.text_state.renderable()
        if cursor:
            renderable.append(self._cursor, style=TYPEWRITER_CURSOR_STYLE)
        self.runtime.set_active_renderable(renderable)

    async def _schedule_status(
        self,
        text: typing.Optional[str],
        *,
        family: StatusFamily,
        delay_sec: float,
    ) -> None:
        """按延迟策略注册状态动画。"""
        if not self.animate:
            return None
        await self.end_status(immediate=True)
        self._pending_status_task = asyncio.create_task(
            self._delayed_status(text, family=family, delay_sec=delay_sec)
        )

    async def _delayed_status(
        self,
        text: typing.Optional[str],
        *,
        family: StatusFamily,
        delay_sec: float,
    ) -> None:
        """等待后显示指定状态并启动节拍驱动。"""
        try:
            await asyncio.sleep(max(0.0, float(delay_sec)))
            self.status_state.set_status(text, family=family, animated=True)
            await self.status_driver.start(reset_phase=True)
            await self._render_status()
            self._pending_status_task = None
        except asyncio.CancelledError:
            return None

    async def _on_status_tick(self, phase: float) -> None:
        """更新状态动画相位。"""
        self.status_state.set_phase(phase)
        await self._render_status()

    async def _render_status(self) -> None:
        """把当前状态帧写入动画专属区域。"""
        if not self.status_state.visible:
            self.runtime.clear_status_renderable()
            return None
        self.runtime.set_status_renderable(
            Design.status_line_renderable(self.status_state.renderable())
        )

    @staticmethod
    def _audit_payload(arguments: dict[str, typing.Any]) -> str:
        """生成工具参数审计 JSON。"""
        try:
            value = sanitize_value(arguments, max_depth=6, max_items=50)
            return json.dumps(
                value,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                default=str,
            )
        except Exception as exc:
            return json.dumps(
                {"error": f"{type(exc).__name__}: {exc}"},
                ensure_ascii=False,
                separators=(",", ":"),
            )


if __name__ == '__main__':
    pass
