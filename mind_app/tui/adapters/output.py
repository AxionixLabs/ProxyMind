# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import json
import math
import time
import random
import typing
import asyncio
from mind_app.output.contracts import OutputControlPort
from mind_app.presentation.models import (
    StyledBlock,
    TextStyle
)
from mind_app.presentation.terminal_text import (
    TerminalTextFilter,
    sanitize_terminal_line,
    sanitize_styled_block,
    sanitize_terminal_text
)
from mind_app.stream_io.output_record import StreamRecordWriter
from mind_app.stream_sanitize import sanitize_value
from ..core.assistant import TuiAssistantStream
from ..core.document import TuiBlockKind
from ..core.runtime import TuiRuntime
from ..core.models import FragmentBlock
from ..core.render import (
    display_line_count,
    fragment_continuation_widths,
    fragments_text
)
from ..core.styles import (
    ASSISTANT_PREFIX_CLASS,
    prompt_style,
    styled_block_fragments
)
from .markdown import render_tui_markdown

TYPEWRITER_CURSOR_STYLE      = TextStyle(foreground="#D7E7FF", bold=True)
STREAM_RENDER_REGULAR_SEC    = 1 / 20
STREAM_RENDER_SLOW_SEC       = 1 / 12
STREAM_RENDER_COST_LIMIT_SEC = STREAM_RENDER_REGULAR_SEC / 4
STREAM_RENDER_LONG_TEXT_SIZE = 2000
STREAM_REVEAL_CELLS_PER_SEC  = 80
STREAM_REVEAL_MAX_LAG_SEC    = 0.2


class TuiOutputControl(OutputControlPort):
    """把单轮流式内容写入持久 TUI。"""

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

        self.assistant     = TuiAssistantStream()
        self.record_writer = StreamRecordWriter(log_file)

        self._assistant_filter = TerminalTextFilter()

        self._cursor = random.choice(("█", "▉", "▋"))

        self._stream_render_handle: asyncio.TimerHandle | None = None

        self._stream_rendered_at: float     = 0.0
        self._stream_render_cost_sec: float = 0.0

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
        """提交当前内容并关闭记录。"""
        _ = blink
        self._finish_assistant_filter(render=False)
        self._commit_current()
        await self.record_writer.close()

    async def append_assistant_delta(
        self,
        chunk: typing.Optional[str],
    ) -> None:
        """向当前 TUI assistant 正文追加一段原始增量。"""
        if not chunk:
            return None

        raw_text = self.assistant.prepare_delta(str(chunk))

        text = self._assistant_filter.feed(raw_text)
        if not text:
            return None

        self.record_writer.write(text)

        if self.animate:
            self.assistant.append(text)
            self._schedule_stream_render()
            return None

        self.assistant.append(text)
        self.assistant.reveal_all()
        self._render_active(cursor=False)

    async def prepare_external_output(self) -> None:
        """在外部展示前提交当前流式内容。"""
        self.assistant.discard_boundary()
        self._finish_assistant_filter(render=False)
        self._commit_current()

    async def settle_stream(self) -> None:
        """立即同步当前流式内容。"""
        self._cancel_stream_render()
        if self.assistant.active:
            self.assistant.reveal_all()
            self._render_active(cursor=False)

    async def record_hidden_output(self, text: str) -> None:
        """记录不直接展示的块状内容。"""
        if text:
            value = sanitize_terminal_text(text)
            self.assistant.discard_boundary()
            self.record_writer.write(value, block=True)

    def mark_stream_boundary(self) -> None:
        """标记下一段流式内容边界。"""
        self._finish_assistant_filter(render=True)
        self.assistant.mark_boundary()

    def record_tool_arguments(
        self,
        name: str,
        arguments: dict[str, typing.Any],
        *,
        call_id: typing.Optional[str] = None,
    ) -> None:
        """记录工具调用参数审计信息。"""
        tool_name = sanitize_terminal_line(name) or "tool"
        safe_call_id = sanitize_terminal_line(call_id)
        call_part = f" call_id={safe_call_id}" if safe_call_id else ""
        self.record_writer.write_audit(
            f"# tool_args tool={tool_name}{call_part} arguments={self._audit_payload(arguments)}"
        )

    async def append_assistant_metadata(
        self,
        text: typing.Optional[str],
    ) -> None:
        """提交正文后追加一项 assistant 元数据块。"""
        if not text:
            return None
        self.assistant.discard_boundary()
        self._commit_current()

        value = sanitize_terminal_text(text)
        self.record_writer.write(value, block=True)
        block = StyledBlock(plain_text=value.rstrip("\n"))
        self.runtime.append_block(
            FragmentBlock(styled_block_fragments(
                block,
            )),
            kind="assistant",
        )

    async def append_presentation_block(
        self,
        block: StyledBlock,
        *,
        block_kind: TuiBlockKind = "operation",
    ) -> None:
        """提交正文后追加一个结构化展示块。"""
        block = sanitize_styled_block(block)
        if not block.plain_text:
            return None

        self.assistant.discard_boundary()
        self._commit_current()
        self.record_writer.write(block.plain_text, block=True)

        self.runtime.append_block(
            FragmentBlock(styled_block_fragments(
                block,
            )),
            kind=block_kind,
        )

    def flush(self) -> None:
        """刷新当前输出记录。"""
        self.record_writer.flush()

    def _commit_current(self) -> bool:
        """把当前动态内容提交为稳定 TUI 内容块并返回提交状态。"""
        self._finish_assistant_filter(render=False)
        self._cancel_stream_render()

        if not self.assistant.active:
            self._assistant_filter.reset()
            self.runtime.clear_active_renderable()
            return False

        text = self.assistant.text

        try:
            rendered = render_tui_markdown(text)
        except (AttributeError, IndexError, KeyError, TypeError, ValueError):
            rendered = FragmentBlock(styled_block_fragments(
                StyledBlock(plain_text=text),
            ))

        block = _assistant_prefixed_block(rendered)

        self.runtime.commit_active_renderable(block)
        self.assistant.clear()
        self._assistant_filter.reset()

        return True

    def _finish_assistant_filter(self, *, render: bool) -> None:
        """收束流式控制序列，并按需刷新新增的换行。"""
        tail = self._assistant_filter.finish()
        if not tail:
            return None

        self.record_writer.write(tail)
        self.assistant.append(tail)
        self.assistant.reveal_all()

        if render:
            self._render_active(cursor=False)

    def _schedule_stream_render(self) -> None:
        """立即展示首帧，并把后续增量合并到自适应帧预算。"""
        loop     = asyncio.get_running_loop()
        now      = loop.time()
        elapsed  = now - self._stream_rendered_at
        interval = self._stream_render_interval()

        if (
            self._stream_render_handle is None
            and (
                self._stream_rendered_at <= 0.0
                or elapsed >= interval
            )
        ):
            self._render_stream_frame()
            self._stream_rendered_at = loop.time()
            self._schedule_pending_stream_frame(loop)
            return None

        if self._stream_render_handle is None:
            self._stream_render_handle = loop.call_later(
                max(0.0, interval - elapsed),
                self._flush_stream_render,
                loop,
            )

    def _flush_stream_render(self, loop: asyncio.AbstractEventLoop) -> None:
        """展示帧预算内合并的最新流式正文。"""
        self._stream_render_handle = None

        if not self.assistant.active:
            self._stream_rendered_at = 0.0
            return None

        self._render_stream_frame()
        self._stream_rendered_at = loop.time()
        self._schedule_pending_stream_frame(loop)

    def _render_stream_frame(self) -> None:
        """渲染流式帧并记录本帧耗时。"""
        started_at = time.perf_counter()

        self.assistant.reveal(self._stream_reveal_cells())
        self._render_active(cursor=True)
        self._stream_render_cost_sec = max(
            0.0,
            time.perf_counter() - started_at,
        )

    def _stream_render_interval(self) -> float:
        """按正文规模和上一帧成本返回流式刷新间隔。"""
        if (
            len(self.assistant.text) >= STREAM_RENDER_LONG_TEXT_SIZE
            or self._stream_render_cost_sec >= STREAM_RENDER_COST_LIMIT_SEC
        ):
            return STREAM_RENDER_SLOW_SEC
        return STREAM_RENDER_REGULAR_SEC

    def _stream_reveal_cells(self) -> int:
        """按帧间隔和积压量返回本帧应揭示的终端列数。"""
        pending = self.assistant.pending_width
        if pending <= 0:
            return int(self.assistant.pending_length > 0)

        interval = self._stream_render_interval()
        regular  = max(1, math.ceil(STREAM_REVEAL_CELLS_PER_SEC * interval))

        lag_frames = max(
            1,
            math.floor(STREAM_REVEAL_MAX_LAG_SEC / interval),
        )

        retained = regular * lag_frames
        return max(regular, pending - retained)

    def _schedule_pending_stream_frame(
        self,
        loop: asyncio.AbstractEventLoop
    ) -> None:
        """在仍有待揭示正文时安排下一帧。"""
        if (
            self.assistant.pending_length <= 0
            or self._stream_render_handle is not None
        ):
            return None
        self._stream_render_handle = loop.call_later(
            self._stream_render_interval(),
            self._flush_stream_render,
            loop,
        )

    def _cancel_stream_render(self) -> None:
        """取消待展示帧并重置流式刷新时钟。"""
        handle = self._stream_render_handle

        self._stream_render_handle   = None
        self._stream_rendered_at     = 0.0
        self._stream_render_cost_sec = 0.0

        if handle is not None:
            handle.cancel()

    def _render_active(
        self,
        *,
        cursor: bool,
    ) -> None:
        """刷新当前流式内容并按需附加打字机光标。"""
        block = StyledBlock(plain_text=self.assistant.visible_text)

        fragments = _assistant_prefixed_fragments(
            list(styled_block_fragments(block))
        )

        if cursor and _cursor_keeps_display_height(
            fragments,
            self._cursor,
            width=self.terminal_width,
        ):
            fragments.append((prompt_style(TYPEWRITER_CURSOR_STYLE), self._cursor))

        self.runtime.set_active_renderable(
            FragmentBlock(tuple(fragments)),
            kind="assistant",
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


def _assistant_prefixed_block(block: FragmentBlock) -> FragmentBlock:
    """给助手正文块添加单个项目符号前缀。"""
    return FragmentBlock(tuple(_assistant_prefixed_fragments(list(block.fragments))))


def _cursor_keeps_display_height(
    fragments: list[tuple[str, str]],
    cursor: str,
    *,
    width: int | None,
) -> bool:
    """判断打字机光标是否不会单独增加正文显示行。"""
    if width is None:
        return True

    text       = fragments_text(fragments)
    line_width = max(1, int(width))

    continuation_widths = fragment_continuation_widths(
        fragments,
        prefix_style=ASSISTANT_PREFIX_CLASS,
        prefix_width=2,
    )

    return display_line_count(
        f"{text}{cursor}",
        width=line_width,
        continuation_widths=continuation_widths,
    ) == display_line_count(
        text,
        width=line_width,
        continuation_widths=continuation_widths,
    )


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

    continuation: bool = False

    for style, text in fragments:
        lines      = text.split("\n")
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
