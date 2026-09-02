# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing

from frontends.tui.contracts.pager import StaticPagerRequest


class StaticPagerViewportPort(typing.Protocol):
    """描述静态页面生命周期需要的滚屏协调能力。"""

    def pause_scrollback(self) -> None:
        """暂停原生滚屏提交。"""
        ...

    def schedule_scrollback_flush(self) -> None:
        """安排全屏页面关闭后的滚屏恢复。"""
        ...


class StaticPagerScreenPort(typing.Protocol):
    """描述 Screen 提供的静态全屏页面能力。"""

    def set_static_pager(
        self,
        active: bool,
        *,
        request: StaticPagerRequest | None = None,
        allow_approval: bool = False,
    ) -> bool:
        """切换静态页面并返回是否发生变化。"""
        ...


class StaticPagerCoordinator(object):
    """协调静态页面与原生终端滚屏的生命周期。"""

    def __init__(
        self,
        *,
        viewport: StaticPagerViewportPort,
        screen: StaticPagerScreenPort,
        cancel_history_backtrack: typing.Callable[[], None],
    ) -> None:
        """绑定页面端口和滚屏协调入口。"""
        self._viewport = viewport
        self._screen = screen
        self._cancel_history_backtrack = cancel_history_backtrack

    def open(
        self,
        request: StaticPagerRequest,
        *,
        allow_approval: bool = False,
    ) -> bool:
        """冻结原生滚屏并打开静态页面。"""
        self._cancel_history_backtrack()
        self._viewport.pause_scrollback()
        try:
            if allow_approval:
                opened = self._screen.set_static_pager(
                    True,
                    request=request,
                    allow_approval=True,
                )
            else:
                opened = self._screen.set_static_pager(True, request=request)
        except BaseException:
            self._viewport.schedule_scrollback_flush()
            raise
        if not opened:
            self._viewport.schedule_scrollback_flush()
        return opened

    def close(self) -> None:
        """关闭静态页面并恢复原生滚屏。"""
        if self._screen.set_static_pager(False):
            self._viewport.schedule_scrollback_flush()


if __name__ == '__main__':
    pass
