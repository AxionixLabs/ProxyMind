# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
import asyncio
import contextlib
from dataclasses import dataclass
from prompt_toolkit.application import (
    Application,
    in_terminal
)
from prompt_toolkit.layout.containers import WindowRenderInfo
from mind_core.config import DEFAULT_SCROLLBACK_REFLOW_LINE_LIMIT
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
from .hyperlinks import decorate_scrollback_hyperlinks
from .styles import ASSISTANT_PREFIX_CLASS


@dataclass(frozen=True, slots=True)
class ScrollbackCandidate(object):
    """保存一次原生滚屏提交依赖的不可变状态。"""
    start_line: int
    line_count: int
    stable_revision: int
    active_revision: int
    display_width: int
    available_height: int
    geometry: tuple[int, int]
    reflow_generation: int
    submitted_query_block: FragmentBlock | None


class TuiTranscriptViewport(object):
    """管理正文视口、分页位置和原生终端滚屏提交。"""

    SCROLLBACK_REFLOW_DEBOUNCE_SEC: typing.Final[float] = 0.08
    STREAM_SCROLLBACK_DEBOUNCE_SEC: typing.Final[float] = 0.08
    STREAM_SCROLLBACK_BATCH_LINES: typing.Final[int]    = 4

    def __init__(
        self,
        *,
        document: TuiDocument,
        is_application_active: typing.Callable[[], bool],
        is_scrollback_deferred: typing.Callable[[], bool],
        is_full_screen_overlay_active: typing.Callable[[], bool],
        is_closing: typing.Callable[[], bool],
        get_application: typing.Callable[[], Application[None]],
        get_terminal_geometry: typing.Callable[[], tuple[int, int]],
        get_terminal_width: typing.Callable[[], int],
        get_available_height: typing.Callable[[], int],
        get_transcript_fragments: typing.Callable[[], FormattedText],
        get_render_info: typing.Callable[[], WindowRenderInfo | None],
        get_render_revision: typing.Callable[[], int],
        clear_terminal_scrollback: typing.Callable[[], None],
        clear_terminal_for_resize_replay: typing.Callable[[], None],
        begin_synchronized_output: typing.Callable[[], bool],
        end_synchronized_output: typing.Callable[[], None],
        settle_canvas_height: typing.Callable[[], None],
        invalidate: typing.Callable[[], None],
        scrollback_reflow_line_limit: int = (
            DEFAULT_SCROLLBACK_REFLOW_LINE_LIMIT
        )
    ) -> None:
        self.document = document

        self._is_application_active        = is_application_active
        self._is_scrollback_deferred       = is_scrollback_deferred
        self._is_full_screen_overlay_active = is_full_screen_overlay_active
        self._is_closing                   = is_closing

        self._get_application          = get_application
        self._get_terminal_geometry    = get_terminal_geometry
        self._get_terminal_width       = get_terminal_width
        self._get_available_height     = get_available_height
        self._get_transcript_fragments = get_transcript_fragments
        self._get_render_info          = get_render_info
        self._get_render_revision      = get_render_revision

        self._clear_terminal_scrollback         = clear_terminal_scrollback
        self._clear_terminal_for_resize_replay = (
            clear_terminal_for_resize_replay
        )
        self._begin_synchronized_output = begin_synchronized_output
        self._end_synchronized_output   = end_synchronized_output
        self._settle_canvas_height      = settle_canvas_height

        self._invalidate = invalidate

        self.scrollback_reflow_line_limit: int = 0

        self.configure_scrollback_reflow_line_limit(
            scrollback_reflow_line_limit
        )

        self.view_row: int | None = None

        self._scrollback_task: asyncio.Task[None] | None  = None
        self._submitted_query_block: FragmentBlock | None = None

        self._observed_geometry: tuple[int, int] | None = None
        self._reflowed_geometry: tuple[int, int] | None = None

        self._reflow_generation: int     = 0
        self._reflow_required: bool      = False
        self._resize_during_stream: bool = False

        self._rendered_revision: int = 0

        self._scrollback_render_revision: int | None                = None
        self._scrollback_reflow_handle: asyncio.TimerHandle | None  = None
        self._scrollback_recheck_handle: asyncio.TimerHandle | None = None
        self._stream_scrollback_handle: asyncio.TimerHandle | None  = None
        self._scrollback_reflow_task: asyncio.Task[None] | None     = None
        self._reflow_reschedule_after_task: bool                    = False

    @property
    def scrollback_task(self) -> asyncio.Task[None] | None:
        """返回当前原生滚屏提交任务。"""
        return self._scrollback_task

    @staticmethod
    def _normalize_geometry(width: int, height: int) -> tuple[int, int]:
        """把终端尺寸限制为有效正整数。"""
        return max(1, int(width)), max(1, int(height))

    @staticmethod
    def _start_stream_scrollback_flush(
        viewport: "TuiTranscriptViewport"
    ) -> None:
        """开始已经完成合并等待的流式滚屏提交。"""
        viewport._stream_scrollback_handle = None
        viewport._start_scrollback_flush()

    def _schedule_scrollback_reflow(
        self,
        *,
        delay: float | None = None
    ) -> None:
        """合并连续尺寸变化并延迟执行原生滚屏重排。"""
        self._cancel_scrollback_reflow()
        if not self._is_application_active() or self._is_closing():
            self._reflowed_geometry = self._observed_geometry
            self._reflow_required = False
            return None

        target_geometry = self._observed_geometry
        if target_geometry is None:
            return None

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return None

        wait = (
            self.SCROLLBACK_REFLOW_DEBOUNCE_SEC
            if delay is None
            else max(0.0, float(delay))
        )
        self._scrollback_reflow_handle = loop.call_later(
            wait,
            self._start_scrollback_reflow,
            target_geometry,
            self._reflow_generation,
        )

    def _scrollback_reflow_pending(self) -> bool:
        """判断观测尺寸是否尚未应用到原生滚屏。"""
        return bool(
            self._reflow_required
            or self._scrollback_reflow_handle is not None
            or (
                self._scrollback_reflow_task is not None
                and not self._scrollback_reflow_task.done()
            )
        )

    def _cancel_scrollback_reflow(self) -> None:
        """取消尚未执行的原生滚屏重排计时器。"""
        handle = self._scrollback_reflow_handle
        self._scrollback_reflow_handle = None
        if handle is not None:
            handle.cancel()

    def _cancel_scrollback_recheck(self) -> None:
        """取消尺寸重排后的终端几何复查。"""
        handle = self._scrollback_recheck_handle
        self._scrollback_recheck_handle = None
        if handle is not None:
            handle.cancel()

    def _cancel_stream_scrollback(self) -> None:
        """取消尚未开始的流式滚屏提交。"""
        handle = self._stream_scrollback_handle
        self._stream_scrollback_handle = None
        if handle is not None:
            handle.cancel()

    def _start_scrollback_reflow(
        self,
        target_geometry: tuple[int, int],
        generation: int
    ) -> None:
        """为稳定尺寸启动唯一的原生滚屏重排任务。"""
        self._scrollback_reflow_handle = None

        task = self._scrollback_reflow_task
        if task is not None and not task.done():
            self._reflow_reschedule_after_task = True
            return None

        application = self._get_application()
        context = application.context
        if context is None:
            return None

        self._scrollback_reflow_task = asyncio.create_task(
            self._reflow_scrollback(target_geometry, generation),
            name="tui scrollback resize reflow",
            context=context.copy(),
        )
        self._scrollback_reflow_task.add_done_callback(
            self._scrollback_reflow_done
        )

    def _scrollback_reflow_done(self, task: asyncio.Task[None]) -> None:
        """回收尺寸重排任务并继续处理仍待应用的最终尺寸。"""
        if self._scrollback_reflow_task is task:
            self._scrollback_reflow_task = None

        try:
            task.result()
        except asyncio.CancelledError:
            pass
        except Exception as exc:
            asyncio.get_running_loop().call_exception_handler({
                "message": "TUI scrollback resize reflow failed",
                "exception": exc,
                "task": task,
            })

        reschedule = self._reflow_reschedule_after_task
        self._reflow_reschedule_after_task = False
        if (
            reschedule
            and self._reflow_required
            and self._scrollback_reflow_handle is None
        ):
            self._schedule_scrollback_reflow(delay=0)
        elif not self._reflow_required:
            self.schedule_scrollback_flush()

    def _complete_scrollback_reflow(
        self,
        target_geometry: tuple[int, int],
    ) -> None:
        """提交一次尺寸重排状态。"""
        self._reflowed_geometry = target_geometry
        self._reflow_required   = False

    def _schedule_scrollback_recheck(
        self,
        target_geometry: tuple[int, int],
    ) -> None:
        """在一次重排后延迟复查终端最终尺寸。"""
        self._cancel_scrollback_recheck()
        if not self._is_application_active() or self._is_closing():
            return None

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return None

        self._scrollback_recheck_handle = loop.call_later(
            self.SCROLLBACK_REFLOW_DEBOUNCE_SEC,
            self._recheck_scrollback_geometry,
            target_geometry,
        )

    def _recheck_scrollback_geometry(
        self,
        target_geometry: tuple[int, int]
    ) -> None:
        """复查重排后的终端尺寸并安排最终修复。"""
        self._scrollback_recheck_handle = None
        current = self._current_geometry()
        if current == target_geometry:
            return None

        self.observe_terminal_geometry(*current)
        self._invalidate()

    def _should_defer_scrollback(self) -> bool:
        """判断当前交互状态是否要求延迟原生滚屏提交。"""
        return bool(
            self._is_scrollback_deferred()
            or self._is_full_screen_overlay_active()
            or (
                self.document.active_block is not None
                and not self.document.active_stream_continuation
            )
            or self.view_row is not None
        )

    def _current_geometry(self) -> tuple[int, int]:
        """读取并规范化当前终端尺寸。"""
        width, height = self._get_terminal_geometry()
        return self._normalize_geometry(width, height)

    def _scrollback_prefix_line_count(
        self,
        *,
        width: int | None = None,
        available_height: int | None = None
    ) -> int:
        """计算可写入滚屏区且不拆分稳定块的逻辑行数量。"""
        if (
            self.document.active_block is not None
            and not self.document.active_stream_continuation
        ):
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

        display_width = max(
            1,
            int(self._get_terminal_width() if width is None else width),
        )

        viewport_height = max(
            0,
            int(
                self._get_available_height()
                if available_height is None
                else available_height
            ),
        )

        available = max(
            0,
            viewport_height - self._live_tail_height(width=display_width),
        )

        if available <= 0:
            retire_count = max_retirable
        else:
            kept_rows: int = 0

            for index in range(len(lines) - 1, -1, -1):
                line = lines[index]
                rows = max(1, display_line_count(
                    fragments_text(line),
                    width=display_width,
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
        retire_count = self.document.complete_scrollback_prefix_line_count(
            required_line_count=retire_count,
            maximum_line_count=max_retirable,
        )

        return self._stream_scrollback_batch(retire_count)

    def _stream_scrollback_batch(self, line_count: int) -> int:
        """流式期间只提交达到批量阈值的完整逻辑行。"""
        line_count = max(0, int(line_count))
        if (
            self.document.active_stream_continuation
            and line_count < self.STREAM_SCROLLBACK_BATCH_LINES
        ):
            return 0
        return line_count

    def _live_tail_height(self, *, width: int | None = None) -> int:
        """返回当前动态正文占用的显示行数。"""
        fragments = self.document.live_fragments()
        if not fragments:
            return 0

        display_width = max(
            1,
            int(self._get_terminal_width() if width is None else width),
        )
        return display_line_count(
            fragments_text(fragments),
            width=display_width,
            continuation_widths=fragment_continuation_widths(
                fragments,
                prefix_style=ASSISTANT_PREFIX_CLASS,
                prefix_width=2,
            ),
        )

    def _schedule_stream_scrollback_flush(self) -> None:
        """合并短时间内连续产生的流式滚屏提交。"""
        if self._stream_scrollback_handle is not None:
            return None
        if not self._scrollback_flush_ready():
            return None

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return None

        self._stream_scrollback_handle = loop.call_later(
            self.STREAM_SCROLLBACK_DEBOUNCE_SEC,
            TuiTranscriptViewport._start_stream_scrollback_flush,
            self,
        )

    def _scrollback_flush_ready(self) -> bool:
        """判断当前是否具备启动原生滚屏提交的条件。"""
        if not self._scrollback_state_ready():
            return False

        task = self._scrollback_task
        return task is None or task.done()

    def _scrollback_state_ready(self) -> bool:
        """判断当前状态是否允许准备原生滚屏批次。"""
        required_revision = self._scrollback_render_revision
        if (
            required_revision is not None
            and self._rendered_revision < required_revision
        ):
            return False

        if self._scrollback_reflow_pending():
            if self._scrollback_reflow_handle is None:
                self._schedule_scrollback_reflow(delay=0)
            return False

        return not (
            self._is_closing()
            or not self._is_application_active()
            or self._should_defer_scrollback()
        )

    def _prepare_scrollback_candidate(
        self
    ) -> ScrollbackCandidate | None:
        """按当前稳定状态准备一次原生滚屏候选。"""
        if not self._scrollback_state_ready():
            return None

        geometry = self._current_geometry()
        if geometry != self._observed_geometry:
            self.observe_terminal_geometry(*geometry)
            if self._scrollback_reflow_pending():
                return None

        display_width    = max(1, int(self._get_terminal_width()))
        available_height = max(0, int(self._get_available_height()))

        line_count = self._scrollback_prefix_line_count(
            width=display_width,
            available_height=available_height,
        )
        if line_count <= 0:
            return None

        return ScrollbackCandidate(
            start_line=self.document.visible_prefix_line_count,
            line_count=line_count,
            stable_revision=self.document.stable_transcript_revision,
            active_revision=self.document.active_transcript_revision,
            display_width=display_width,
            available_height=available_height,
            geometry=geometry,
            reflow_generation=self._reflow_generation,
            submitted_query_block=self._submitted_query_block,
        )

    def _scrollback_candidate_still_valid(
        self,
        candidate: ScrollbackCandidate
    ) -> bool:
        """校验滚屏候选依赖的文档与终端状态是否未变化。"""
        if not self._scrollback_state_ready():
            return False

        if (
            candidate.start_line != self.document.visible_prefix_line_count
            or candidate.stable_revision
            != self.document.stable_transcript_revision
            or candidate.active_revision
            != self.document.active_transcript_revision
            or candidate.reflow_generation != self._reflow_generation
            or candidate.geometry != self._observed_geometry
            or candidate.submitted_query_block
            is not self._submitted_query_block
        ):
            return False

        current_geometry = self._current_geometry()
        if current_geometry != candidate.geometry:
            self.observe_terminal_geometry(*current_geometry)
            return False

        return bool(
            candidate.display_width == max(1, int(self._get_terminal_width()))
            and candidate.available_height
            == max(0, int(self._get_available_height()))
        )

    def _start_scrollback_flush(self) -> None:
        """在条件仍有效时启动唯一的原生滚屏任务。"""
        if not self._scrollback_flush_ready():
            return None

        candidate = self._prepare_scrollback_candidate()
        if candidate is None:
            self._scrollback_render_revision = None
            return None

        context = self._get_application().context
        if context is None:
            return None

        self._scrollback_render_revision = None
        self._scrollback_task = asyncio.create_task(
            self._flush_scrollback(candidate),
            name="tui scrollback flush",
            context=context.copy(),
        )

    def _require_stable_render(self) -> None:
        """记录包含最新稳定正文的下一次应用渲染。"""
        self._invalidate()

        revision = max(0, int(self._get_render_revision()))
        if self._is_application_active():
            revision += 1

        self._scrollback_render_revision = max(
            self._scrollback_render_revision or 0,
            revision,
        )

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
        self._require_stable_render()
        self.schedule_scrollback_flush()

    def stable_content_changed(self) -> None:
        """在动态正文提交或清除后安排滚屏。"""
        self._require_stable_render()
        self.schedule_scrollback_flush()

    def stream_content_changed(self) -> None:
        """在流式稳定前缀提交后合并安排滚屏。"""
        self._require_stable_render()
        self._schedule_stream_scrollback_flush()

    def observe_render_revision(self, revision: int) -> None:
        """记录已经写入终端的应用帧并继续待处理滚屏。"""
        self._rendered_revision = max(
            self._rendered_revision,
            max(0, int(revision)),
        )
        if self._scrollback_render_revision is not None:
            self.schedule_scrollback_flush()

    def configure_scrollback_reflow_line_limit(self, value: int) -> None:
        """设置恢复和几何重排允许回放的最大逻辑行数。"""
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            raise ValueError(
                "scrollback reflow line limit must be a positive integer"
            )
        self.scrollback_reflow_line_limit = value

    def prepare_restored_scrollback(self) -> None:
        """把恢复记录的主界面回放范围限制为最近一段。"""
        self.document.prepare_scrollback_tail(
            max_line_count=self.scrollback_reflow_line_limit,
        )

    def observe_terminal_geometry(self, width: int, height: int) -> None:
        """记录终端尺寸并在稳定后安排原生滚屏重排。"""
        current = self._normalize_geometry(width, height)
        if current == self._observed_geometry:
            return None

        self._cancel_scrollback_recheck()
        self._observed_geometry = current
        if self._reflowed_geometry is None:
            self._reflowed_geometry = current
            return None

        self._reflow_generation += 1
        if current == self._reflowed_geometry:
            self._cancel_scrollback_reflow()
            self._reflow_required = False
            return None

        self._reflow_required = True
        self._schedule_scrollback_reflow()

    def stream_finalized(self) -> None:
        """在尺寸重排涉及流式尾部时安排最终稳定正文回放。"""
        if not self._resize_during_stream:
            return None

        self._resize_during_stream = False
        if self._observed_geometry is None:
            return None

        self._reflow_generation += 1
        self._reflow_required = True
        self._schedule_scrollback_reflow(delay=0)

    def refresh_geometry(self) -> bool:
        """在终端恢复后重新读取尺寸并刷新正文布局。"""
        previous = self._observed_geometry
        current  = self._current_geometry()

        self.observe_terminal_geometry(*current)
        self._invalidate()

        if current == previous:
            self.schedule_scrollback_flush()

        return current != previous

    def schedule_scrollback_flush(self) -> None:
        """在稳定正文超出实时视口时安排原生滚屏提交。"""
        if self._scrollback_render_revision is None:
            self._scrollback_render_revision = self._rendered_revision

        if self.document.active_stream_continuation:
            self._schedule_stream_scrollback_flush()
            return None

        self._cancel_stream_scrollback()
        self._start_scrollback_flush()

    def pause_scrollback(self) -> None:
        """取消正在等待的原生滚屏提交。"""
        self._cancel_stream_scrollback()
        task = self._scrollback_task
        if task is not None and not task.done():
            task.cancel()

    def clear_visible(self) -> None:
        """清理当前终端画布并保留完整会话归档。"""
        self._cancel_stream_scrollback()
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

    def _print_scrollback_fragments(self, fragments: FormattedText) -> None:
        """打印滚屏批次并把物理光标推进到下一行行首。"""
        self._get_application().print_text([
            *decorate_scrollback_hyperlinks(fragments),
            ("", "\n"),
        ])

    async def _flush_scrollback(
        self,
        candidate: ScrollbackCandidate
    ) -> None:
        """原子提交溢出的稳定正文并推进文档提交游标。"""
        current_task = asyncio.current_task()

        reschedule: bool = False

        try:
            if not self._scrollback_candidate_still_valid(candidate):
                candidate = self._prepare_scrollback_candidate()
                if candidate is None:
                    return None

            synchronized: bool = False

            try:
                synchronized = self._begin_synchronized_output()

                async with in_terminal(render_cli_done=False):
                    if not self._scrollback_candidate_still_valid(candidate):
                        candidate = self._prepare_scrollback_candidate()
                        if candidate is None:
                            return None

                    if (
                        self.document.visible_prefix_line_count
                        != candidate.start_line
                    ):
                        return None

                    fragments = self.document.scrollback_prefix_fragments(
                        candidate.line_count
                    )

                    self._print_scrollback_fragments(fragments)

                    if not self.document.commit_scrollback_prefix(
                        candidate.line_count,
                        expected_start=candidate.start_line,
                    ):
                        raise AssertionError(
                            "scrollback prefix changed during commit"
                        )

                    self._settle_canvas_height()
                    self.view_row = None

                    current_geometry = self._current_geometry()
                    if current_geometry != candidate.geometry:
                        self.observe_terminal_geometry(*current_geometry)
            finally:
                if synchronized:
                    self._end_synchronized_output()

        except asyncio.CancelledError:
            reschedule = True
            raise

        except (EOFError, OSError, RuntimeError):
            return None

        finally:
            if self._scrollback_task is current_task:
                self._scrollback_task = None
                if (
                    reschedule
                    or self._scrollback_render_revision is not None
                ):
                    self.schedule_scrollback_flush()
            self._invalidate()

    async def _cancel_scrollback_task(self) -> None:
        """取消并等待普通原生滚屏提交任务退出。"""
        task = self._scrollback_task
        if (
            task is None
            or task.done()
            or task is asyncio.current_task()
        ):
            return None

        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

    async def _reflow_scrollback(
        self,
        target_geometry: tuple[int, int],
        generation: int
    ) -> None:
        """按最终终端尺寸集中清屏并回放有界稳定内容。"""
        if generation != self._reflow_generation:
            return None

        settled_geometry = self._current_geometry()
        if settled_geometry != self._observed_geometry:
            self.observe_terminal_geometry(*settled_geometry)
            return None
        if settled_geometry != target_geometry:
            return None

        if (
            target_geometry != self._observed_geometry
            or not self._reflow_required
        ):
            return None
        if self._should_defer_scrollback():
            return None

        had_native_scrollback = bool(
            self.document.scrollback_line_count
            > self.document.cleared_line_count
        )
        source_reflowed = self.document.set_display_width(
            target_geometry[0],
            reflow_sources=True,
        )
        if source_reflowed:
            self.view_row = None
            self._invalidate()

        if not had_native_scrollback:
            self._complete_scrollback_reflow(target_geometry)
            self._schedule_scrollback_recheck(target_geometry)
            return None

        await self._cancel_scrollback_task()
        if (
            generation != self._reflow_generation
            or target_geometry != self._observed_geometry
            or self._should_defer_scrollback()
        ):
            return None

        synchronized: bool = False
        completed: bool    = False

        try:
            synchronized = self._begin_synchronized_output()

            async with in_terminal(render_cli_done=False):
                if (
                    generation != self._reflow_generation
                    or target_geometry != self._observed_geometry
                    or self._should_defer_scrollback()
                ):
                    return None

                previous_scrollback_position = (
                    self.document.scrollback_line_count
                )

                try:
                    self.document.rewind_scrollback(
                        max_line_count=self.scrollback_reflow_line_limit,
                    )
                    self.view_row = None

                    self._clear_terminal_for_resize_replay()

                    line_count = self._scrollback_prefix_line_count()
                    if line_count > 0:
                        start_line = self.document.visible_prefix_line_count

                        fragments = self.document.scrollback_prefix_fragments(
                            line_count
                        )

                        self._print_scrollback_fragments(fragments)

                        if not self.document.commit_scrollback_prefix(
                            line_count,
                            expected_start=start_line,
                        ):
                            raise RuntimeError(
                                "scrollback replay position changed"
                            )
                        self._settle_canvas_height()

                    self._complete_scrollback_reflow(target_geometry)
                    if (
                        self.document.active_kind == "assistant"
                        and self.document.active_stream_continuation
                    ):
                        self._resize_during_stream = True
                    completed = True
                except BaseException:
                    self.document.restore_scrollback_position(
                        previous_scrollback_position
                    )
                    raise
        finally:
            if synchronized:
                self._end_synchronized_output()
            if completed:
                self._schedule_scrollback_recheck(target_geometry)
            elif (
                generation == self._reflow_generation
                and target_geometry == self._observed_geometry
            ):
                self._reflow_required = True
                self._invalidate()

    async def close(self) -> None:
        """取消并等待全部原生滚屏任务结束。"""
        self._cancel_scrollback_reflow()
        self._cancel_scrollback_recheck()
        self._cancel_stream_scrollback()

        tasks = tuple(
            task
            for task in (
                self._scrollback_task,
                self._scrollback_reflow_task,
            )
            if task is not None
        )
        for task in tasks:
            if not task.done():
                task.cancel()
        for task in tasks:
            with contextlib.suppress(asyncio.CancelledError):
                await task


if __name__ == '__main__':
    pass
