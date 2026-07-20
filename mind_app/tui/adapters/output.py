# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import json
import random
import typing
import asyncio
from mind_app.output.contracts import (
    BLOCK_OUTPUT,
    STREAM_OUTPUT,
    OutputDisplay,
    OutputPort
)
from mind_app.presentation.models import (
    StyledBlock,
    TextSpan,
    TextStyle
)
from mind_app.stream_io.output_record import StreamRecordWriter
from mind_app.stream_render.animation import AnimDriver
from mind_app.stream_sanitize import sanitize_value
from mind_app.stream_state.boundary import OutputBoundaryState
from mind_app.stream_state.text import TextState
from ..core.activity import (
    StatusFamily,
    TuiStatusState
)
from ..core.runtime import TuiRuntime
from ..core.models import FragmentBlock
from ..core.styles import (
    ASSISTANT_PREFIX_CLASS,
    prompt_style,
    styled_block_fragments
)
from .markdown import render_tui_final

TYPEWRITER_CURSOR_STYLE = TextStyle(foreground="#D7E7FF", bold=True)


class TuiOutputControl(OutputPort):
    """把单轮流式内容和状态写入持久 TUI。"""

    BLOCK: OutputDisplay  = BLOCK_OUTPUT
    STREAM: OutputDisplay = STREAM_OUTPUT

    def __init__(
        self,
        log_file: str,
        *,
        runtime: TuiRuntime,
        animate: bool = True,
    ) -> None:
        self.log_file = log_file
        self.runtime  = runtime
        self.animate  = bool(animate)

        self.text_state    = TextState(width_provider=lambda: self.terminal_width)
        self.record_writer = StreamRecordWriter(log_file)
        self.status_state  = TuiStatusState()

        self.status_driver = AnimDriver(
            is_active=lambda: self.status_state.animating,
            get_interval=self.status_state.interval,
            get_phase_rate=self.status_state.phase_rate,
            on_tick=self._on_status_tick,
        )
        self._pending_status_task: asyncio.Task[None] | None = None
        self._stream_boundary_pending: bool                  = False

        self._document_boundary = OutputBoundaryState(stream_display=self.STREAM)

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
        display_style: TextStyle | None = None,
        display_parts: list[TextSpan] | None = None,
        preserve_display_parts: bool = False,
    ) -> None:
        """向当前 TUI 内容块追加一段流式或块状文本。"""
        if not chunk:
            return None

        text            = str(chunk)
        boundary_prefix = self._consume_stream_boundary_prefix(incoming_text=text)
        raw_chunk       = None

        if boundary_prefix and display == self.STREAM:
            text = f"{boundary_prefix}{text}"
            raw_chunk = text

        self.record_writer.write(text, block=(display == self.BLOCK))
        if display == self.STREAM:
            await self.end_status(immediate=True)
            if (
                echo
                and self.text_state.display_text
                and not self.text_state.stream_only
            ):
                self._commit_current()

        starts_document_block = not self.text_state.display_text
        if echo and starts_document_block:
            self._prepare_document_block(display)

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
            self._observe_document_output(display)
            return None

        self.text_state.append(
            text,
            echo=echo,
            display=display,
            display_chunk=display_chunk,
            raw_chunk=raw_chunk,
            display_style=display_style,
            display_parts=display_parts,
            preserve_display_parts=preserve_display_parts,
        )
        if echo:
            self._observe_document_output(display)
            self._render_active(cursor=False)

    async def prepare_external_output(self) -> None:
        """在外部展示前提交当前流式内容。"""
        self._consume_stream_boundary_prefix()
        had_output = bool(
            self.text_state.display_text
            or self._document_boundary.last_display is not None
        )
        self._commit_current()
        if had_output:
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
            value = str(text)
            self._consume_stream_boundary_prefix(incoming_text=value)
            self.record_writer.write(value, block=True)

    def mark_stream_boundary(self) -> None:
        """标记下一段流式内容边界。"""
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
        display_parts: list[TextSpan] | None = None,
    ) -> None:
        """提交当前内容后追加一个直接展示块。"""
        if not chunk:
            return None
        self._consume_stream_boundary_prefix(incoming_text=str(chunk))
        self._commit_current()

        text = str(chunk)

        self.record_writer.write(text, block=True)
        self._prepare_document_block(self.BLOCK)

        spans = tuple(display_parts or ())
        block = StyledBlock(
            plain_text=text.rstrip("\n"),
            spans=spans,
        )
        self.runtime.append_block(FragmentBlock(styled_block_fragments(
            block,
            fallback_style=TextStyle(bold=True),
        )))
        self._observe_document_output(self.BLOCK)

    async def append_styled_block(
        self,
        block: StyledBlock,
        renderable: FragmentBlock,
    ) -> None:
        """记录并追加一个已由 TUI 适配器格式化的展示块。"""
        if not block.plain_text:
            return None
        self._consume_stream_boundary_prefix(incoming_text=block.plain_text)
        self._commit_current()
        self.record_writer.write(block.plain_text, block=True)
        self._prepare_document_block(self.BLOCK)
        self.runtime.append_block(renderable)
        self._observe_document_output(self.BLOCK)

    def flush(self) -> None:
        """刷新当前输出记录。"""
        self.record_writer.flush()

    def _commit_current(self) -> bool:
        """把当前动态内容提交为稳定 TUI 内容块并返回提交状态。"""
        if not self.text_state.display_text:
            self.runtime.clear_active_renderable()
            return False
        stream_only = self.text_state.stream_only
        block = render_tui_final(self.text_state.final_units())
        if stream_only:
            block = _assistant_prefixed_block(block)
        self.runtime.commit_active_renderable(block)
        self.text_state.clear()
        return True

    def _consume_stream_boundary_prefix(
        self,
        *,
        incoming_text: str | None = None,
    ) -> str:
        """消费流式结束边界，并返回下一段正文需要补充的换行。"""
        if not self._stream_boundary_pending:
            return ""

        self._stream_boundary_pending = False
        if incoming_text and incoming_text.startswith("\n"):
            return ""

        source = self.text_state.display_text
        if not source:
            return ""

        trailing = OutputBoundaryState.count_trailing_newlines(source)
        return "\n" * max(0, 1 - trailing)

    def _prepare_document_block(self, display: OutputDisplay) -> None:
        """把共享输出边界转换为正文块前的视觉空行。"""
        prefix = self._document_boundary.prefix(
            for_display=display,
            incoming_text="",
        )
        if prefix:
            self.runtime.append_gap()

    def _observe_document_output(self, display: OutputDisplay) -> None:
        """记录正文块最后一种显示类型及其稳定行尾。"""
        self._document_boundary.observe_display(display=display, text="\n")

    async def _append_typewriter(self, text: str) -> None:
        """按现有打字机节奏分批展示流式文本。"""
        size = max(1, len(text))
        for index in range(0, len(text), 2):
            delta = text[index:index + 2]
            self.text_state.append(delta, display=self.STREAM)
            self._render_active(cursor=True)

            progress = index / max(1, size - 1)
            delay    = 0.010 + (0.0065 - 0.010) * progress

            if any(char in "。.!！?？" for char in delta):
                delay += 0.035
            elif any(char in "；;：:" for char in delta):
                delay += 0.025
            elif any(char in "，," for char in delta):
                delay += 0.015
            await asyncio.sleep(max(0.0015, delay))

    def _render_active(self, *, cursor: bool) -> None:
        """刷新当前流式内容并按需附加打字机光标。"""
        fragments = list(styled_block_fragments(self.text_state.visible_block()))
        if self.text_state.stream_only:
            fragments = _assistant_prefixed_fragments(fragments)
        if cursor:
            fragments.append((prompt_style(TYPEWRITER_CURSOR_STYLE), self._cursor))
        self.runtime.set_active_renderable(FragmentBlock(tuple(fragments)))

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
        self.runtime.set_status_renderable(self.status_state.render_block())

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


def _assistant_prefixed_block(block: FragmentBlock) -> FragmentBlock:
    """给助手正文块添加单个项目符号前缀。"""
    return FragmentBlock(tuple(_assistant_prefixed_fragments(list(block.fragments))))


def _assistant_prefixed_fragments(
    fragments: list[tuple[str, str]],
) -> list[tuple[str, str]]:
    """移除正文前导换行并添加助手项目符号和续行缩进。"""
    out = [(style, text) for style, text in fragments if text]
    while out:
        style, text = out[0]
        trimmed = text.lstrip("\r\n")
        if trimmed:
            out[0] = style, trimmed
            break
        out.pop(0)
    if not out:
        return []
    prefix_style = ASSISTANT_PREFIX_CLASS
    return [
        (prefix_style, "• "),
        *_assistant_continuation_fragments(out, indent_style=prefix_style),
    ]


def _assistant_continuation_fragments(
    fragments: list[tuple[str, str]],
    *,
    indent_style: str,
) -> list[tuple[str, str]]:
    """在助手正文每个显式续行前补充两个空格。"""
    out: list[tuple[str, str]] = []
    continuation = False

    for style, text in fragments:
        lines = text.split("\n")
        last_index = len(lines) - 1
        for index, line in enumerate(lines):
            has_newline = index < last_index
            if continuation and (line or has_newline):
                out.append((indent_style, "  "))
                continuation = False
            if line:
                out.append((style, line))
            if has_newline:
                out.append((style, "\n"))
                continuation = True

    return out


if __name__ == '__main__':
    pass
