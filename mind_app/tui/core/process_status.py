# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import time
import typing
import asyncio
from prompt_toolkit.utils import get_cwidth
from .models import FormattedText
from .render import clip_fragments
from .status_frames import render_status_fragments

PROCESS_STATUS_REFRESH_SEC = 1 / 12


class TuiProcessStatus(object):
    """持有后台进程单行状态并生成格式化片段。"""

    def __init__(
        self,
        *,
        invalidate: typing.Callable[[], None],
        get_width: typing.Callable[[], int],
    ) -> None:
        self._invalidate = invalidate
        self._get_width  = get_width
        self.label: str  = ""
        self._started_at: float = 0.0
        self._animation_handle: asyncio.TimerHandle | None = None

    @property
    def active(self) -> bool:
        """返回当前是否存在可展示的后台进程。"""
        return bool(self.label)

    def set_label(self, value: typing.Any) -> None:
        """更新后台进程摘要文本。"""
        label = " ".join(str(value or "").split())
        if label == self.label:
            return None
        was_active = self.active
        self.label = label
        if self.active and not was_active:
            self._started_at = time.monotonic()
            self._schedule_tick()
        elif not self.active:
            self._started_at = 0.0
            self._cancel_tick()
        self._invalidate()

    def clear(self) -> None:
        """清空后台进程摘要文本。"""
        self.set_label("")

    def fragments(self) -> FormattedText:
        """生成不超过终端宽度的单行状态。"""
        if not self.label:
            return []

        width = max(1, int(self._get_width()))
        suffix: FormattedText = [
            ("class:process-status.separator", " · "),
            ("class:process-status.action", "/ps"),
            ("class:process-status.hint", " to view"),
        ]
        suffix_width = sum(get_cwidth(text) for _style, text in suffix)
        status_width = max(1, width - suffix_width)
        phase = max(0.0, time.monotonic() - self._started_at)
        status = clip_fragments(
            render_status_fragments(
                f"exec {self.label}",
                family="wait",
                phase=phase,
                animated=True,
            ),
            width=status_width,
        )
        return clip_fragments([*status, *suffix], width=width)

    def _schedule_tick(self) -> None:
        """安排后台命令状态的下一次动画刷新。"""
        if not self.active or self._animation_handle is not None:
            return None
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return None
        self._animation_handle = loop.call_later(
            PROCESS_STATUS_REFRESH_SEC,
            self._tick,
        )

    def _tick(self) -> None:
        """推进后台命令状态动画并请求重绘。"""
        self._animation_handle = None
        if not self.active:
            return None
        self._invalidate()
        self._schedule_tick()

    def _cancel_tick(self) -> None:
        """取消后台命令状态的动画刷新。"""
        handle = self._animation_handle
        self._animation_handle = None
        if handle is not None:
            handle.cancel()


if __name__ == '__main__':
    pass
