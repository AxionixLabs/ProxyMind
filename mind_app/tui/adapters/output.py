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
from ..core.document import (
    TranscriptCellSource,
    TuiBlockKind
)
from ..core.runtime import TuiRuntime
from ..core.models import FragmentBlock
from ..core.render import (
    display_line_count,
    fragment_continuation_widths,
    fragments_text
)
from ..core.styles import (
    ASSISTANT_PREFIX_CLASS,
    assistant_block,
    assistant_continuation_block,
    prompt_style,
    styled_block_fragments
)
from .markdown import render_tui_markdown

TYPEWRITER_CURSOR_STYLE      = TextStyle(foreground="#D7E7FF", bold=True)
STREAM_RENDER_REGULAR_SEC    = 1 / 20
STREAM_RENDER_SLOW_SEC       = 1 / 12
STREAM_RENDER_VERY_SLOW_SEC  = 1 / 8
STREAM_RENDER_COST_LIMIT_SEC = STREAM_RENDER_REGULAR_SEC / 4
STREAM_RENDER_LONG_TEXT_SIZE = 2000
STREAM_RENDER_HUGE_TEXT_SIZE = 50_000
STREAM_REVEAL_CELLS_PER_SEC  = 80
STREAM_REVEAL_MAX_LAG_SEC    = 0.2
STREAM_STABLE_HOLDBACK_LINES = 3


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

        self._stream_rendered_at: float         = 0.0
        self._stream_render_cost_sec: float     = 0.0
        self._stream_stable_end: int            = 0
        self._final_block: FragmentBlock | None = None

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

        self._final_block = None

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
        self._finish_assistant_filter(render=False)

        if self.assistant.active:
            self.assistant.reveal_all()
            self._stabilize_stream_prefix()
            self._final_block = self._render_final_block()
            self.runtime.set_active_renderable(
                self._final_block,
                kind="assistant",
                raw_text=self._active_stream_text(),
                stream_continuation=self._stream_stable_end > 0,
            )

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
        transcript_block: StyledBlock | None = None,
        source: TranscriptCellSource | None = None,
        raw_text: str | None = None
    ) -> None:
        """提交正文后追加一个结构化展示块。"""
        block            = sanitize_styled_block(block)
        transcript_block = sanitize_styled_block(transcript_block or block)

        if not block.plain_text:
            return None

        self.assistant.discard_boundary()
        self._commit_current()
        self.record_writer.write(block.plain_text, block=True)

        self.runtime.append_block(
            FragmentBlock(styled_block_fragments(
                block,
                hyperlinks=self.runtime.hyperlinks_enabled,
            )),
            kind=block_kind,
            transcript_block=FragmentBlock(styled_block_fragments(
                transcript_block,
                hyperlinks=self.runtime.hyperlinks_enabled,
            )),
            source=source,
            raw_text=raw_text,
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

        block = self._final_block or self._render_final_block()

        self.runtime.commit_active_renderable(
            block,
            raw_text=self._active_stream_text(),
        )
        self.assistant.clear()
        self._assistant_filter.reset()
        self._stream_stable_end = 0
        self._final_block = None

        return True

    def _render_final_block(self) -> FragmentBlock:
        """把当前完整正文渲染为可直接提交的最终块。"""
        text = self._active_stream_text()

        return self._render_markdown_block(
            text,
            continuation=self._stream_stable_end > 0,
        )

    def _render_markdown_block(
        self,
        text: str,
        *,
        continuation: bool
    ) -> FragmentBlock:
        """把一段稳定 Markdown 正文渲染为助手展示块。"""

        try:
            rendered = render_tui_markdown(
                text,
                hyperlinks=self.runtime.hyperlinks_enabled,
            )
        except (AttributeError, IndexError, KeyError, TypeError, ValueError):
            rendered = FragmentBlock(styled_block_fragments(
                StyledBlock(plain_text=text),
            ))

        if continuation:
            return assistant_continuation_block(rendered)
        return assistant_block(rendered)

    def _finish_assistant_filter(self, *, render: bool) -> None:
        """收束流式控制序列，并按需刷新新增的换行。"""
        tail = self._assistant_filter.finish()
        if not tail:
            return None

        self.record_writer.write(tail)
        self._final_block = None
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
        """渲染流式帧并记录本次展示耗时。"""
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
            len(self.assistant.text) >= STREAM_RENDER_HUGE_TEXT_SIZE
            or self._stream_render_cost_sec >= STREAM_RENDER_REGULAR_SEC
        ):
            return STREAM_RENDER_VERY_SLOW_SEC

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
        cursor: bool
    ) -> bool:
        """刷新当前流式内容并返回打字机光标是否可见。"""
        self._stabilize_stream_prefix()

        visible_text = self._active_stream_text(visible=True)

        block = FragmentBlock(styled_block_fragments(
            StyledBlock(plain_text=visible_text),
        ))

        rendered = (
            assistant_continuation_block(block)
            if self._stream_stable_end > 0
            else assistant_block(block)
        )

        fragments = list(rendered.fragments)

        cursor_visible = bool(
            cursor
            and len(visible_text) < STREAM_RENDER_LONG_TEXT_SIZE
            and _cursor_keeps_display_height(
                fragments,
                self._cursor,
                width=self.terminal_width,
            )
        )
        if cursor_visible:
            fragments.append((prompt_style(TYPEWRITER_CURSOR_STYLE), self._cursor))

        self.runtime.set_active_renderable(
            FragmentBlock(tuple(fragments)),
            kind="assistant",
            raw_text=visible_text,
            stream_continuation=self._stream_stable_end > 0,
        )

        return cursor_visible

    def _stabilize_stream_prefix(self) -> None:
        """提交完整逻辑行并只保留少量可变正文尾部。"""
        cut = self.assistant.revealed_end
        for _ in range(STREAM_STABLE_HOLDBACK_LINES):
            cut = self.assistant.text.rfind(
                "\n",
                self._stream_stable_end,
                cut,
            )
            if cut < self._stream_stable_end:
                return None

        cut += 1
        if cut <= self._stream_stable_end:
            return None

        text = self.assistant.text[self._stream_stable_end:cut]

        block = self._render_markdown_block(
            text,
            continuation=self._stream_stable_end > 0,
        )
        self.runtime.set_active_renderable(
            block,
            kind="assistant",
            raw_text=text,
            stream_continuation=self._stream_stable_end > 0,
        )
        self.runtime.commit_active_stream_prefix(block, raw_text=text)
        self._stream_stable_end = cut

    def _active_stream_text(self, *, visible: bool = False) -> str:
        """返回尚未稳定提交的完整或可见正文尾部。"""
        end = (
            self.assistant.revealed_end
            if visible
            else len(self.assistant.text)
        )
        return self.assistant.text[self._stream_stable_end:end]

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


def _cursor_keeps_display_height(
    fragments: list[tuple[str, str]],
    cursor: str,
    *,
    width: int | None
) -> bool:
    """判断打字机光标是否不会单独增加正文显示行。"""
    if width is None:
        return True

    last_line  = _last_formatted_line(fragments)
    text       = fragments_text(last_line)
    line_width = max(1, int(width))

    continuation_widths = fragment_continuation_widths(
        last_line,
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


def _last_formatted_line(
    fragments: list[tuple[str, str]]
) -> list[tuple[str, str]]:
    """返回格式化正文的最后一个逻辑行。"""
    line: list[tuple[str, str]] = []

    for style, text in fragments:
        if "\n" not in text:
            line.append((style, text))
            continue

        line = []
        tail = text.rsplit("\n", 1)[1]
        if tail:
            line.append((style, tail))

    return line


if __name__ == '__main__':
    pass
