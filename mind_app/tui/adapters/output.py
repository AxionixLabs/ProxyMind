# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import json
import time
import typing
import asyncio
from functools import partial
from mind_app.output.contracts import OutputControlPort
from mind_app.presentation.models import StyledBlock
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
    TuiBlockKind,
    WidthBlockRenderer
)
from ..core.runtime import TuiRuntime
from ..core.models import (
    FormattedText,
    FragmentBlock
)
from ..core.render import (
    join_formatted_lines,
    wrap_formatted_lines
)
from ..core.styles import (
    assistant_block,
    styled_block_fragments
)
from .markdown import (
    TuiMarkdownStreamRenderer,
    render_tui_assistant_markdown,
)

STREAM_RENDER_REGULAR_SEC    = 1 / 20
STREAM_RENDER_SLOW_SEC       = 1 / 12
STREAM_RENDER_VERY_SLOW_SEC  = 1 / 8
STREAM_RENDER_COST_LIMIT_SEC = STREAM_RENDER_REGULAR_SEC / 4
STREAM_RENDER_LONG_TEXT_SIZE = 2000
STREAM_RENDER_HUGE_TEXT_SIZE = 50_000
STREAM_CATCH_UP_LINES        = 8
STREAM_CATCH_UP_SEC          = 0.12
STREAM_RESIZE_DEBOUNCE_SEC   = 0.08


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
        self._markdown_stream  = TuiMarkdownStreamRenderer()

        self._stream_render_handle: asyncio.TimerHandle | None = None
        self._stream_resize_handle: asyncio.TimerHandle | None = None

        self._stream_rendered_at: float     = 0.0
        self._stream_render_cost_sec: float = 0.0
        self._stream_source_end: int           = 0
        self._stream_width: int                = 0
        self._stream_block                     = FragmentBlock(())
        self._stream_rows: list[FormattedText] = []
        self._stream_visible_rows: int         = 0
        self._stream_oldest_pending_at: float | None = None
        self._before_render_registered = True

        self.runtime.screen.application.before_render += (
            self._sync_stream_width
        )

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
        try:
            self._finish_assistant_filter(render=False)
            self._commit_current()
            await self.record_writer.close()
        finally:
            self._cancel_stream_resize()
            self._unregister_before_render()

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
            if self._collect_complete_source_lines():
                self._schedule_stream_render()
            return None

        self.assistant.append(text)
        if self._collect_complete_source_lines():
            self._reveal_all_stream_rows()

    async def prepare_external_output(self) -> None:
        """在外部展示前提交当前流式内容。"""
        self.assistant.discard_boundary()
        self._finish_assistant_filter(render=False)
        self._commit_current()

    async def settle_stream(self) -> None:
        """渲染当前未换行尾部并立即揭示全部完整显示行。"""
        self._cancel_stream_render()
        self._finish_assistant_filter(render=False)

        if self.assistant.active:
            self._refresh_stream_rows(len(self.assistant.text))
            self._reveal_all_stream_rows()

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
        call_id: typing.Optional[str] = None
    ) -> None:
        """记录工具调用参数审计信息。"""
        tool_name    = sanitize_terminal_line(name) or "tool"
        safe_call_id = sanitize_terminal_line(call_id)
        call_part    = f" call_id={safe_call_id}" if safe_call_id else ""

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
        raw_text: str | None = None,
        display_renderer: WidthBlockRenderer | None = None,
        display_render_width: int | None = None
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
            display_renderer=display_renderer,
            display_render_width=display_render_width,
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
            self._reset_stream_state()
            self.runtime.clear_active_renderable()
            return False

        block = self._render_final_block()

        with self.runtime.screen.visual_update():
            if self.runtime.document.active_block is None:
                self.runtime.set_active_renderable(
                    block,
                    kind="assistant",
                    raw_text=self._active_stream_text(),
                )

            self.runtime.commit_active_renderable(
                block,
                raw_text=self._active_stream_text(),
                source_renderer=partial(
                    render_tui_assistant_markdown,
                    hyperlinks=self.runtime.hyperlinks_enabled,
                ),
                source_render_width=self.terminal_width,
            )
        self.assistant.clear()
        self._assistant_filter.reset()
        self._reset_stream_state()

        return True

    def _render_final_block(self) -> FragmentBlock:
        """把当前完整正文渲染为可直接提交的最终块。"""
        text = self._active_stream_text()

        return self._render_markdown_block(text)

    def _render_markdown_block(
        self,
        text: str,
    ) -> FragmentBlock:
        """把一段稳定 Markdown 正文渲染为助手展示块。"""
        return render_tui_assistant_markdown(
            text,
            self.terminal_width,
            hyperlinks=self.runtime.hyperlinks_enabled,
        )

    def _finish_assistant_filter(self, *, render: bool) -> None:
        """收束流式控制序列，并按需刷新新增的换行。"""
        tail = self._assistant_filter.finish()
        if not tail:
            return None

        self.record_writer.write(tail)
        self.assistant.append(tail)

        if render:
            if self._collect_complete_source_lines():
                if self.animate:
                    self._schedule_stream_render()
                else:
                    self._reveal_all_stream_rows()

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
        """揭示完整显示行并记录本次展示耗时。"""
        started_at = time.perf_counter()

        pending = len(self._stream_rows) - self._stream_visible_rows
        if pending <= 0:
            return None

        now = time.monotonic()
        catch_up = bool(
            pending >= STREAM_CATCH_UP_LINES
            or (
                self._stream_oldest_pending_at is not None
                and now - self._stream_oldest_pending_at >= STREAM_CATCH_UP_SEC
            )
        )
        self._stream_visible_rows = (
            len(self._stream_rows)
            if catch_up
            else self._stream_visible_rows + 1
        )
        self._render_visible_stream_rows()

        if self._stream_visible_rows >= len(self._stream_rows):
            self._stream_oldest_pending_at = None

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

    def _schedule_pending_stream_frame(
        self,
        loop: asyncio.AbstractEventLoop
    ) -> None:
        """在仍有待揭示显示行时安排下一帧。"""
        if (
            self._stream_visible_rows >= len(self._stream_rows)
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

    def _collect_complete_source_lines(self) -> bool:
        """把新收到的完整源码行送入增量 Markdown 渲染器。"""
        newline = self.assistant.text.rfind("\n", self._stream_source_end)
        if newline < self._stream_source_end:
            return False
        return self._refresh_stream_rows(newline + 1)

    def _refresh_stream_rows(self, source_end: int) -> bool:
        """按最新完整源码前缀生成可排队的终端显示行。"""
        end = max(
            self._stream_source_end,
            min(len(self.assistant.text), source_end),
        )
        width = max(1, int(self.terminal_width) - 2)
        if end == self._stream_source_end and width == self._stream_width:
            return False
        if end == self._stream_source_end:
            self._rewrap_stream_rows(width)
            return True

        source = self.assistant.text[:end]
        block = self._render_stream_block(source, width=width)

        self._stream_source_end = end
        self._stream_block      = block
        self._replace_stream_rows(
            wrap_formatted_lines(list(block.fragments), width=width),
            width=width,
        )

        if self._stream_visible_rows < len(self._stream_rows):
            if self._stream_oldest_pending_at is None:
                self._stream_oldest_pending_at = time.monotonic()
        else:
            self._stream_oldest_pending_at = None
        return True

    def _rewrap_stream_rows(self, width: int) -> None:
        """在终端宽度变化时从完整源码重建活动显示行。"""
        block = self._render_stream_block(
            self.assistant.text[:self._stream_source_end],
            width=max(1, int(width)),
        )
        self._stream_block = block
        rows = wrap_formatted_lines(
            list(block.fragments),
            width=max(1, int(width)),
        )
        had_visible_rows = self._stream_visible_rows > 0
        self._replace_stream_rows(
            rows,
            width=max(1, int(width)),
            render_visible=False,
        )
        if had_visible_rows:
            self._stream_visible_rows = len(rows)
            self._stream_oldest_pending_at = None
            self._render_visible_stream_rows()

    def _render_stream_block(
        self,
        source: str,
        *,
        width: int,
    ) -> FragmentBlock:
        """按当前内容宽度渲染一份增量 Markdown 快照。"""
        try:
            return self._markdown_stream.render(
                source,
                hyperlinks=False,
                width=width,
            )
        except (AttributeError, IndexError, KeyError, TypeError, ValueError):
            self._markdown_stream.reset()
            return FragmentBlock(styled_block_fragments(
                StyledBlock(plain_text=source),
            ))

    def _replace_stream_rows(
        self,
        rows: list[FormattedText],
        *,
        width: int,
        render_visible: bool = True,
    ) -> None:
        """替换显示行快照，并原子刷新已经可见的可变尾部。"""
        old_visible = self._stream_rows[:self._stream_visible_rows]

        self._stream_width = width
        self._stream_rows  = rows
        self._stream_visible_rows = min(
            self._stream_visible_rows,
            len(rows),
        )

        visible_changed = bool(
            self._stream_visible_rows
            and old_visible != rows[:self._stream_visible_rows]
        )
        if visible_changed and render_visible:
            self._render_visible_stream_rows()

    def _sync_stream_width(self, _application: typing.Any) -> None:
        """在布局计算前记录稳定后的活动正文目标宽度。"""
        width = max(1, int(self.terminal_width) - 2)
        if not self._stream_source_end or width == self._stream_width:
            self._cancel_stream_resize()
            return None

        handle = self._stream_resize_handle
        if handle is not None:
            handle.cancel()

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            self._flush_stream_resize(width)
            return None

        self._stream_resize_handle = loop.call_later(
            STREAM_RESIZE_DEBOUNCE_SEC,
            self._flush_stream_resize,
            width,
        )

    def _flush_stream_resize(self, width: int) -> None:
        """在终端尺寸稳定后按最终宽度重排活动 Markdown。"""
        self._stream_resize_handle = None
        if (
            self._stream_source_end
            and width != self._stream_width
        ):
            self._rewrap_stream_rows(width)

    def _cancel_stream_resize(self) -> None:
        """取消等待中的活动 Markdown 尺寸重排。"""
        handle = self._stream_resize_handle
        self._stream_resize_handle = None
        if handle is not None:
            handle.cancel()

    def _unregister_before_render(self) -> None:
        """移除当前单轮输出注册的布局回调。"""
        if not self._before_render_registered:
            return None
        self.runtime.screen.application.before_render -= self._sync_stream_width
        self._before_render_registered = False

    def _reveal_all_stream_rows(self) -> None:
        """一次揭示当前已经渲染完成的全部显示行。"""
        if not self._stream_rows:
            return None
        self._stream_visible_rows = len(self._stream_rows)
        self._stream_oldest_pending_at = None
        self._render_visible_stream_rows()

    def _render_visible_stream_rows(self) -> bool:
        """把变化后的完整显示行作为一个动态正文快照上屏。"""
        visible = join_formatted_lines(
            self._stream_rows[:self._stream_visible_rows]
        )
        block = assistant_block(FragmentBlock(tuple(visible)))
        raw_text = self.assistant.text[:self._stream_source_end]

        if (
            self.runtime.document.active_kind == "assistant"
            and self.runtime.document.active_block == block
            and self.runtime.document.active_raw_text == raw_text
        ):
            return False

        self.runtime.set_active_renderable(
            block,
            kind="assistant",
            raw_text=raw_text,
        )
        return True

    def _reset_stream_state(self) -> None:
        """清空当前增量渲染和显示行队列。"""
        self._cancel_stream_resize()
        self._markdown_stream.reset()

        self._stream_source_end        = 0
        self._stream_width             = 0
        self._stream_block             = FragmentBlock(())
        self._stream_rows              = []
        self._stream_visible_rows      = 0
        self._stream_oldest_pending_at = None

    def _active_stream_text(self) -> str:
        """返回当前完整流式正文。"""
        return self.assistant.text

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
