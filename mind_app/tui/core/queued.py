# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import collections
from dataclasses import dataclass
from prompt_toolkit.utils import get_cwidth
from mind_app.presentation.terminal_text import sanitize_terminal_text
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

    def fragments(self, *, width: int, max_rows: int = 6) -> FormattedText:
        """生成动画行下方的待提交消息列表。"""
        if not self._items:
            return []

        row_limit = max(1, int(max_rows))

        lines: list[FormattedText] = [[(
            "class:queue.label",
            _queue_title(width),
        )]]

        available     = max(0, row_limit - 1)
        visible_count = min(len(self._items), available)

        if len(self._items) > available:
            visible_count = max(0, available - 1)

        for item in list(self._items)[:visible_count]:
            preview = " ".join(sanitize_terminal_text(item.visible_text).split())
            lines.append([
                ("class:queue.marker", "  ↳ "),
                ("class:queue.text", preview),
            ])

        hidden_count = len(self._items) - visible_count

        if hidden_count:
            lines.append([(
                "class:queue.more",
                f"    … {hidden_count} more",
            )])

        out: FormattedText = []

        for index, line in enumerate(lines):
            if index:
                out.append(("", "\n"))
            out.extend(_ellipsize_fragments(line, width=max(1, width)))

        return out


def _queue_title(width: int) -> str:
    """返回适合当前终端宽度的队列标题。"""
    titles = (
        "• Messages queued for the next turn (Esc edits latest)",
        "• Queued for next turn (Esc edits latest)",
        "• Queued for next turn",
    )

    limit = max(1, int(width))

    for title in titles:
        if get_cwidth(title) <= limit:
            return title

    return titles[-1]


def _ellipsize_fragments(parts: FormattedText, *, width: int) -> FormattedText:
    """按显示宽度裁剪单行片段并在末尾添加省略号。"""
    limit = max(1, int(width))

    if get_cwidth("".join(text for _, text in parts)) <= limit:
        return parts

    omit = "…"

    clipped = clip_fragments(
        parts,
        width=max(0, limit - get_cwidth(omit)),
    )

    style = clipped[-1][0] if clipped else "class:queue.text"

    return [*clipped, (style, omit)]


if __name__ == '__main__':
    pass
