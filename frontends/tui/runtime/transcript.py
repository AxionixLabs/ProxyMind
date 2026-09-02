# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import contextlib
import typing

from frontends.tui.contracts.text import FragmentBlock
from ..core.document import (
    SourceBlockRenderer,
    TranscriptCellSource,
    TranscriptBlock,
    TuiBlockKind,
    TuiDocument,
    WidthBlockRenderer
)
from ..core.transcript_overlay import TuiTranscriptOverlay
from ..core.viewport import TuiTranscriptViewport


class TranscriptScreenPort(typing.Protocol):
    """描述单个 Runtime 会话内 Screen 提供的 transcript 画面能力。"""

    def visual_update(self) -> contextlib.AbstractContextManager[None]:
        """返回合并当前 Screen 重绘请求的视觉事务。"""
        ...

    def invalidate(self) -> None:
        """请求当前 Screen 重绘。"""
        ...

    def clear_terminal_scrollback(self) -> None:
        """清除当前 Screen 对应终端的原生滚屏。"""
        ...

    def set_transcript_overlay(self, active: bool) -> bool:
        """切换当前 Screen 的 transcript overlay 并返回是否变化。"""
        ...


class TranscriptCoordinator(object):
    """拥有正文替换、动态提交和 transcript/viewport 通知顺序。"""

    def __init__(
        self,
        *,
        document: TuiDocument,
        viewport: TuiTranscriptViewport,
        overlay: TuiTranscriptOverlay,
        screen: TranscriptScreenPort,
        flush_background_blocks: typing.Callable[[], None],
        discard_background_blocks: typing.Callable[[], None],
    ) -> None:
        self._document = document
        self._viewport = viewport
        self._overlay = overlay
        self._screen = screen
        self._flush_background_blocks = flush_background_blocks
        self._discard_background_blocks = discard_background_blocks

    def replace(self, blocks: typing.Iterable[TranscriptBlock]) -> None:
        """替换稳定正文并重置原生滚屏、viewport 和 overlay 缓存。"""
        self._viewport.pause_scrollback()
        self._document.replace_blocks(blocks)
        self._viewport.prepare_restored_scrollback()

        self._discard_background_blocks()

        self._viewport.reset_view()
        self._overlay.content_replaced()
        self._screen.clear_terminal_scrollback()
        self._viewport.stable_content_changed()

    def set_active(
        self,
        block: FragmentBlock,
        *,
        kind: TuiBlockKind,
        transcript_block: FragmentBlock | None,
        source: TranscriptCellSource | None,
        raw_text: str | None,
        stream_continuation: bool,
        gap_before: int | None,
        display_renderer: WidthBlockRenderer | None,
        display_render_width: int | None
    ) -> bool:
        """替换动态正文并请求一次稳定画布重绘。"""
        with self._screen.visual_update():
            changed = self._document.set_active(
                block,
                kind=kind,
                transcript_block=transcript_block,
                source=source,
                raw_text=raw_text,
                stream_continuation=stream_continuation,
                gap_before=gap_before,
                display_renderer=display_renderer,
                display_render_width=display_render_width,
            )
            self._overlay.content_changed()
            if changed:
                self._screen.invalidate()
        return changed

    def commit_active(
        self,
        block: FragmentBlock,
        *,
        transcript_block: FragmentBlock | None,
        source: TranscriptCellSource | None,
        raw_text: str | None,
        source_renderer: SourceBlockRenderer | None,
        source_render_width: int | None,
        display_renderer: WidthBlockRenderer | None,
        display_render_width: int | None,
        stable_id: str | None = None
    ) -> TranscriptBlock:
        """把动态正文提交为稳定块并清理流式状态。"""
        with self._screen.visual_update():
            assistant_stream = self._document.active_kind == "assistant"
            committed = self._document.commit_active(
                block,
                transcript_block=transcript_block,
                source=source,
                raw_text=raw_text,
                source_renderer=source_renderer,
                source_render_width=source_render_width,
                display_renderer=display_renderer,
                display_render_width=display_render_width,
                stable_id=stable_id,
            )
            self._overlay.content_changed()
            if assistant_stream:
                self._viewport.stream_finalized()
            self._viewport.stable_content_changed()
            self._flush_background_blocks()
            return committed

    def commit_stream_prefix(
        self,
        block: FragmentBlock,
        *,
        raw_text: str,
        source_renderer: SourceBlockRenderer | None,
        source_render_width: int | None
    ) -> None:
        """提交当前流式正文的稳定前缀并保持执行周期。"""
        self._document.commit_active(
            block,
            raw_text=raw_text,
            source_renderer=source_renderer,
            source_render_width=source_render_width,
        )
        self._overlay.content_changed()
        self._viewport.stream_content_changed()

    def clear_active(self) -> None:
        """清空动态正文并恢复稳定 transcript 状态。"""
        assistant_stream = self._document.active_kind == "assistant"
        self._document.clear_active()
        self._overlay.content_changed()
        if assistant_stream:
            self._viewport.stream_finalized()
        self._viewport.stable_content_changed()
        self._flush_background_blocks()


class TranscriptOverlayCoordinator(object):
    """协调 transcript overlay、原生滚屏和历史编辑入口的生命周期。"""

    def __init__(
        self,
        *,
        overlay: TuiTranscriptOverlay,
        viewport: TuiTranscriptViewport,
        screen: TranscriptScreenPort,
        cancel_history_backtrack: typing.Callable[[], None]
    ) -> None:
        self._overlay = overlay
        self._viewport = viewport
        self._screen = screen

        self._cancel_history_backtrack = cancel_history_backtrack

    def toggle(self) -> None:
        """切换完整记录画面并协调原生滚屏任务。"""
        self._cancel_history_backtrack()
        if not self._overlay.active:
            self._open()
            return None

        if self._screen.set_transcript_overlay(False):
            self._viewport.schedule_scrollback_flush()

    def open_backtrack(self) -> None:
        """打开完整记录并选择最近可编辑用户轮次。"""
        if not self._open():
            return None

        if self._overlay.begin_or_step_backtrack():
            return None

        self._screen.set_transcript_overlay(False)
        self._viewport.schedule_scrollback_flush()

    def _open(self) -> bool:
        """冻结原生滚屏并打开完整记录画面。"""
        self._viewport.pause_scrollback()
        try:
            opened = self._screen.set_transcript_overlay(True)
        except BaseException:
            self._viewport.schedule_scrollback_flush()
            raise

        if not opened:
            self._viewport.schedule_scrollback_flush()
        return opened


if __name__ == '__main__':
    pass
