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

        self._is_application_active  = is_application_active
        self._is_scrollback_deferred = is_scrollback_deferred
        self._is_closing             = is_closing

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

    def discard_submitted_query(self) -> None:
        """在二级交互接管时撤下刚提交的用户输入块。"""
        block = self._submitted_query_block

        self._submitted_query_block = None

        if block is not None and self.document.discard_trailing_block(block):
            self.view_row = None
            self._invalidate()

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
            or self._is_scrollback_deferred()
            or self.document.active_block is not None
            or self.view_row is not None
            or (task is not None and not task.done())
            or self._scrollback_prefix_count() <= 0
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

    async def _flush_scrollback(self) -> None:
        """原子提交溢出的稳定正文并推进文档提交游标。"""
        current_task = asyncio.current_task()

        reschedule: bool = False

        try:
            while self._is_application_active() and not self._is_closing():
                if (
                    self.document.active_block is not None
                    or self.view_row is not None
                ):
                    return None

                async with in_terminal(render_cli_done=False):
                    count = self._scrollback_prefix_count()
                    if count <= 0:
                        return None

                    fragments = self.document.scrollback_prefix_fragments(count)
                    retained  = self.document.visible_blocks[count:]

                    separator = (
                        "\n\n"
                        if not retained or retained[0].gap_before
                        else "\n"
                    )
                    self._get_application().print_text([
                        *fragments,
                        ("", separator),
                    ])
                    self.document.commit_scrollback_prefix(count)
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

    def _scrollback_prefix_count(self) -> int:
        """计算当前需要提交到原生滚屏区的稳定块数量。"""
        if self.document.active_block is not None:
            return 0

        blocks    = self.document.visible_blocks
        submitted = self._submitted_query_block

        if blocks and submitted is not None and blocks[-1].block is submitted:
            blocks = blocks[:-1]

        if not blocks:
            return 0

        available  = self._get_available_height()
        kept_rows  = 0
        first_kept = len(blocks)

        for index in range(len(blocks) - 1, -1, -1):
            text = fragments_text(blocks[index].block.fragments).strip("\r\n")

            block_rows = display_line_count(
                text,
                width=self._get_terminal_width(),
                continuation_widths=fragment_continuation_widths(
                    list(blocks[index].block.fragments),
                    prefix_style=ASSISTANT_PREFIX_CLASS,
                    prefix_width=2,
                ),
            )

            separator_rows = (
                1
                if first_kept < len(blocks) and blocks[first_kept].gap_before
                else 0
            )

            candidate = block_rows + separator_rows + kept_rows
            if candidate > available:
                break

            kept_rows  = candidate
            first_kept = index

        return first_kept

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
