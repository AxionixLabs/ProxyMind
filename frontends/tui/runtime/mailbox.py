# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
from frontends.tui.contracts.screen import MailboxScreenPort
from frontends.tui.contracts.transcript import MailboxEntry
from ..core.mailbox import TuiMailboxOverlay
from ..core.viewport import TuiTranscriptViewport


class MailboxOverlayCoordinator(object):
    """拥有 mailbox 快照访问、详情等待和滚屏恢复顺序。"""

    def __init__(
        self,
        *,
        overlay: TuiMailboxOverlay,
        viewport: TuiTranscriptViewport,
        screen: MailboxScreenPort,
        cancel_history_backtrack: typing.Callable[[], None]
    ) -> None:
        self._overlay  = overlay
        self._viewport = viewport
        self._screen   = screen

        self._cancel_history_backtrack = cancel_history_backtrack

    @property
    def entries(self) -> tuple[MailboxEntry, ...]:
        """返回已经过终端文本过滤的 mailbox 展示快照。"""
        return self._overlay.entries

    def update(
        self,
        entries: typing.Iterable[MailboxEntry],
        *,
        listener_active: bool,
    ) -> None:
        """把远端请求快照同步到当前 Screen。"""
        self._screen.set_mailbox_entries(
            entries,
            listener_active=listener_active,
        )

    def close(self) -> None:
        """关闭详情画面并解除等待方。"""
        self._screen.set_mailbox_overlay(False)

    async def view_entry(
        self,
        entry_key: str,
        *,
        allow_menu: bool,
    ) -> bool:
        """冻结原生滚屏并等待单条消息详情关闭。"""
        self._cancel_history_backtrack()
        self._viewport.pause_scrollback()

        try:
            opened = self._screen.set_mailbox_overlay(
                True,
                entry_key=entry_key,
                allow_menu=allow_menu,
            )
        except BaseException:
            self._viewport.schedule_scrollback_flush()
            raise

        if not opened:
            self._viewport.schedule_scrollback_flush()
            return False

        try:
            await self._overlay.wait_closed()
        finally:
            if self._overlay.active:
                self._screen.set_mailbox_overlay(False)
            self._viewport.schedule_scrollback_flush()
        return True


if __name__ == '__main__':
    pass
