# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
from .models import FormattedText
from .render import clip_fragments


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

        return clip_fragments(
            [
                ("class:process-status.label", "exec"),
                ("class:process-status.separator", " · "),
                ("class:process-status.command", self.label),
            ],
            width=self._get_width(),
        )


if __name__ == '__main__':
    pass
