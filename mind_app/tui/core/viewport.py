# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
import asyncio
import contextlib
from prompt_toolkit.application import (
    Application,
    in_terminal
)
from prompt_toolkit.layout.containers import WindowRenderInfo
from .document import TuiDocument
from .models import (
    FormattedText,
    FragmentBlock
)
from .render import (
    display_line_count,
    fragment_continuation_widths,
    fragments_text
)
from .styles import ASSISTANT_PREFIX_CLASS


class TuiTranscriptViewport(object):
    """管理正文视口、分页位置和原生终端滚屏提交。"""

    def __init__(
        self,
        *,
        document: TuiDocument,
        is_application_active: typing.Callable[[], bool],
        is_scrollback_deferred: typing.Callable[[], bool],
        is_transcript_overlay_active: typing.Callable[[], bool],
        is_closing: typing.Callable[[], bool],
        get_application: typing.Callable[[], Application[None]],
        get_terminal_width: typing.Callable[[], int],
        get_available_height: typing.Callable[[], int],
        get_transcript_fragments: typing.Callable[[], FormattedText],
        get_render_info: typing.Callable[[], WindowRenderInfo | None],
        clear_terminal_scrollback: typing.Callable[[], None],
        invalidate: typing.Callable[[], None]
    ) -> None:
        self.document = document

        self._is_application_active        = is_application_active
        self._is_scrollback_deferred       = is_scrollback_deferred
        self._is_transcript_overlay_active = is_transcript_overlay_active
        self._is_closing                   = is_closing

        self._get_application          = get_application
        self._get_terminal_width       = get_terminal_width
        self._get_available_height     = get_available_height
        self._get_transcript_fragments = get_transcript_fragments
        self._get_render_info          = get_render_info

        self._clear_terminal_scrollback = clear_terminal_scrollback

        self._invalidate = invalidate

        self.view_row: int | None = None

        self._scrollback_task: asyncio.Task[None] | None  = None
        self._submitted_query_block: FragmentBlock | None = None

    @property
    def scrollback_task(self) -> asyncio.Task[None] | None:
        """返回当前原生滚屏提交任务。"""
        return self._scrollback_task

    def reset_view(self) -> None:
        """让正文视口恢复跟随最新输出。"""
        self.view_row = None

    def mark_submitted_query(self, block: FragmentBlock) -> None:
        """标记刚提交且应暂时保留在实时画布中的用户输入。"""
        self._submitted_query_block = block

    def clear_submitted_query(self) -> None:
        """清除刚提交用户输入的保留标记。"""
        self._submitted_query_block = None

    def discard_submitted_query(self) -> bool:
        """在二级交互接管时撤下刚提交的用户输入块。"""
        block = self._submitted_query_block

        self._submitted_query_block = None

        if block is not None and self.document.discard_trailing_block(block):
            self.view_row = None
            self._invalidate()
            return True

        return False

    def content_appended(self) -> None:
        """在稳定正文追加后清除提交标记并安排滚屏。"""
        self._submitted_query_block = None
        self._invalidate()
        self.schedule_scrollback_flush()

    def stable_content_changed(self) -> None:
        """在动态正文提交或清除后安排滚屏。"""
        self._invalidate()
        self.schedule_scrollback_flush()

    def schedule_scrollback_flush(self) -> None:
        """在稳定正文超出实时视口时安排原生滚屏提交。"""
        task = self._scrollback_task
        if (
            self._is_closing()
            or not self._is_application_active()
            or self._should_defer_scrollback()
            or (task is not None and not task.done())
            or self._scrollback_prefix_line_count() <= 0
        ):
            return None

        context = self._get_application().context
        if context is None:
            return None

        self._scrollback_task = asyncio.create_task(
            self._flush_scrollback(),
            name="tui scrollback flush",
            context=context.copy(),
        )

    def pause_scrollback(self) -> None:
        """取消正在等待的原生滚屏提交。"""
        task = self._scrollback_task
        if task is not None and not task.done():
            task.cancel()

    async def _flush_scrollback(self) -> None:
        """原子提交溢出的稳定正文并推进文档提交游标。"""
        current_task = asyncio.current_task()

        reschedule: bool = False

        try:
            while self._is_application_active() and not self._is_closing():
                if self._should_defer_scrollback():
                    return None

                async with in_terminal(render_cli_done=False):
                    if self._should_defer_scrollback():
                        return None

                    line_count = self._scrollback_prefix_line_count()
                    if line_count <= 0:
                        return None

                    fragments = self.document.scrollback_prefix_fragments(
                        line_count
                    )
                    # print_text 统一补一个结尾换行，批次只提供行间换行。
                    self._get_application().print_text(fragments)
                    self.document.commit_scrollback_prefix(line_count)
                    self.view_row = None

        except asyncio.CancelledError:
            reschedule = True
            raise

        except (EOFError, OSError, RuntimeError):
            return None

        finally:
            if self._scrollback_task is current_task:
                self._scrollback_task = None
                if reschedule:
                    self.schedule_scrollback_flush()
            self._invalidate()

    def _should_defer_scrollback(self) -> bool:
        """判断当前交互状态是否要求延迟原生滚屏提交。"""
        return bool(
            self._is_scrollback_deferred()
            or self._is_transcript_overlay_active()
            or self.document.active_block is not None
            or self.view_row is not None
        )

    def _scrollback_prefix_line_count(self) -> int:
        """计算可写入滚屏区的完整稳定逻辑行数量。"""
        if self.document.active_block is not None:
            return 0

        lines     = self.document.visible_stable_lines()
        submitted = self._submitted_query_block

        if not lines:
            return 0

        max_retirable = len(lines)
        if submitted is not None:
            submitted_offset = self.document.visible_line_offset_for_block(
                submitted
            )
            if submitted_offset is not None:
                max_retirable = min(max_retirable, submitted_offset)
        if max_retirable <= 0:
            return 0

        available = max(0, self._get_available_height())
        if available <= 0:
            return max_retirable

        kept_rows: int = 0

        for index in range(len(lines) - 1, -1, -1):
            line = lines[index]
            rows = max(1, display_line_count(
                fragments_text(line),
                width=self._get_terminal_width(),
                continuation_widths=fragment_continuation_widths(
                    line,
                    prefix_style=ASSISTANT_PREFIX_CLASS,
                    prefix_width=2,
                ),
            ))

            candidate = rows + kept_rows
            if candidate > available:
                retire_count = min(index + 1, len(lines) - 1)
                break

            kept_rows = candidate
        else:
            return 0

        retire_count = min(retire_count, max_retirable)

        while (
            retire_count < max_retirable
            and not fragments_text(lines[retire_count]).strip()
        ):
            retire_count += 1

        return retire_count

    def clear_visible(self) -> None:
        """清理当前终端画布并保留完整会话归档。"""
        task = self._scrollback_task
        if task is not None and not task.done():
            task.cancel()

        self.document.clear_visible_prefix()
        self.view_row = None
        self._clear_terminal_scrollback()

    def scroll_page(self, direction: int) -> None:
        """按当前正文窗口高度向前或向后翻页。"""
        fragments = self._get_transcript_fragments()

        text = fragments_text(fragments)
        if not text:
            return None

        total_rows = display_line_count(
            text,
            width=self._get_terminal_width(),
            continuation_widths=fragment_continuation_widths(
                fragments,
                prefix_style=ASSISTANT_PREFIX_CLASS,
                prefix_width=2,
            ),
        )

        render_info = self._get_render_info()

        window_height = (
            render_info.window_height
            if render_info is not None
            else max(1, self._get_available_height())
        )

        if total_rows <= window_height:
            self.view_row = None
            return None

        last_row = max(0, total_rows - 1)

        current_row = (
            last_row
            if self.view_row is None
            else min(self.view_row, last_row)
        )

        page_rows  = max(1, window_height - 1)
        target_row = max(0, min(last_row, current_row + direction * page_rows))

        self.view_row = None if target_row >= last_row else target_row

        self._invalidate()

        if self.view_row is None:
            self.schedule_scrollback_flush()

    async def close(self) -> None:
        """等待当前原生滚屏任务结束。"""
        task = self._scrollback_task
        if task is not None:
            with contextlib.suppress(asyncio.CancelledError):
                await task


if __name__ == '__main__':
    pass
