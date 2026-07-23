# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
from prompt_toolkit.utils import get_cwidth
from .models import FormattedText
from .render import clip_fragments
from .status_frames import render_status_fragments


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

    @property
    def active(self) -> bool:
        """返回当前是否存在可展示的后台进程。"""
        return bool(self.label)

    def set_label(self, value: typing.Any) -> None:
        """更新后台进程摘要文本。"""
        label = " ".join(str(value or "").split())
        if label == self.label:
            return None
        self.label = label
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
        status = clip_fragments(
            render_status_fragments(
                f"exec {self.label}",
                family="wait",
                phase=0.0,
                animated=False,
            ),
            width=status_width,
        )
        return clip_fragments([*status, *suffix], width=width)


if __name__ == '__main__':
    pass
