# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import collections
from dataclasses import dataclass
from .models import FormattedText
from .render import clip_fragments


@dataclass(frozen=True, slots=True)
class TuiSubmission(object):
    """保存一次输入提交的请求文本和可见编辑状态。"""

    value: str
    editable_text: str
    paste_store: dict[str, str]
    shell_mode: bool = False

    @property
    def visible_text(self) -> str:
        """返回适合正文、队列和历史展示的输入文本。"""
        if not self.shell_mode:
            return self.editable_text
        command = self.editable_text.strip()
        return f"! {command}" if command else "!"


class TuiQueuedMessages(object):
    """管理执行期间等待下一轮处理的输入消息。"""

    def __init__(self) -> None:
        self._items: collections.deque[TuiSubmission] = collections.deque()

    @property
    def active(self) -> bool:
        """返回当前是否存在待提交消息。"""
        return bool(self._items)

    def append(self, item: TuiSubmission) -> None:
        """在队尾追加一条待提交消息。"""
        self._items.append(item)

    def pop_next(self) -> TuiSubmission | None:
        """取出下一条应交给会话循环的消息。"""
        if not self._items:
            return None
        return self._items.popleft()

    def pop_last(self) -> TuiSubmission | None:
        """撤回最近一条待提交消息。"""
        if not self._items:
            return None
        return self._items.pop()

    def fragments(self, *, width: int) -> FormattedText:
        """生成动画行下方的待提交消息列表。"""
        if not self._items:
            return []

        count = len(self._items)
        label = "Queued message" if count == 1 else f"Queued messages ({count})"

        lines: list[FormattedText] = [[("class:queue.label", f"• {label}")]]

        for item in self._items:
            preview = " ".join(item.visible_text.split())
            lines.append([
                ("class:queue.marker", "  ↳ "),
                ("class:queue.text", preview),
            ])

        out: FormattedText = []
        for index, line in enumerate(lines):
            if index:
                out.append(("", "\n"))
            out.extend(clip_fragments(line, width=max(1, width)))
        return out


if __name__ == '__main__':
    pass
