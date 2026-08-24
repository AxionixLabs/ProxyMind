# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import time
import typing
import asyncio
from mind_app.presentation.terminal_text import sanitize_terminal_text
from .models import FormattedText
from ..rendering.fragments import clip_fragments
from .status_frames import (
    render_status_fragments,
    status_indicator_fragment,
    status_interval
)


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
        self._animation_task: asyncio.Task[None] | None = None
        self._animated: bool = False

    @property
    def active(self) -> bool:
        """返回当前是否存在可展示的后台进程。"""
        return bool(self.label)

    def set_label(self, value: typing.Any) -> None:
        """更新后台进程摘要文本。"""
        label = " ".join(sanitize_terminal_text(value).split())
        if label == self.label:
            if label:
                self._ensure_animation_task()
            return None
        self.label = label
        if label:
            self._ensure_animation_task()
        else:
            self._stop_animation_task()
        self._invalidate()

    def clear(self) -> None:
        """清空后台进程摘要文本。"""
        self.set_label("")

    def fragments(self) -> FormattedText:
        """生成不超过终端宽度的单行状态。"""
        if not self.label:
            return []

        width = max(1, int(self._get_width()))
        return clip_fragments(self._label_fragments(), width=width)

    def inline_fragments(self) -> FormattedText:
        """生成附着在活动状态行末尾的进程摘要。"""
        if not self.label:
            return []

        return [
            ("class:process-status.background", " · "),
            *self._label_fragments()[2:],
        ]

    def _label_fragments(self) -> FormattedText:
        """生成状态正文，并为后台终端提示应用静态弱化样式。"""
        base, separator, ps_action, stop_action = self._label_parts()
        label_fragments = render_status_fragments(
            base,
            family="wait",
            phase=0.0,
            animated=False,
        )
        if self._animated:
            label_fragments[0] = status_indicator_fragment(
                time.perf_counter(),
                family="wait",
                animated=True,
            )

        if not separator:
            return label_fragments

        dim_class = "class:process-status.background"
        return [
            *label_fragments[:2],
            (dim_class, base),
            (dim_class, separator),
            (dim_class, ps_action),
            (dim_class, " to view"),
            (dim_class, separator),
            (dim_class, stop_action),
            (dim_class, " to close"),
        ]

    def _label_parts(self) -> tuple[str, str, str, str]:
        """拆分后台终端状态正文和操作入口。"""
        marker = " · /ps to view · /stop to close"
        if self.label.endswith(marker):
            return (
                self.label[:-len(marker)],
                " · ",
                "/ps",
                "/stop",
            )
        return self.label, "", "", ""

    def _ensure_animation_task(self) -> None:
        """在事件循环中启动独立的状态行刷新任务。"""
        task = self._animation_task
        if task is not None and not task.done():
            return None

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            self._animated = False
            return None

        self._animated = True
        self._animation_task = loop.create_task(
            self._animation_loop(),
            name="process status animation",
        )

    def _stop_animation_task(self) -> None:
        """停止状态行刷新并恢复静态渲染。"""
        self._animated = False
        task = self._animation_task
        self._animation_task = None
        if task is not None and not task.done():
            task.cancel()

    async def _animation_loop(self) -> None:
        """按状态动画节奏请求重绘，不读取或修改进程快照。"""
        try:
            while self.label:
                self._invalidate()
                await asyncio.sleep(status_interval("wait"))
        except asyncio.CancelledError:
            return None


if __name__ == '__main__':
    pass
