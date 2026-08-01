# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import copy
import typing
import collections
from dataclasses import (
    dataclass,
    field,
    replace
)
from prompt_toolkit.utils import get_cwidth
from mind_nova.identifiers import short_uid
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
    client_message_id: str = field(default_factory=lambda: short_uid(16))
    attachments: tuple[dict[str, typing.Any], ...] = ()
    extras: dict[str, typing.Any] = field(default_factory=dict)
    payload_bound: bool = False
    server_queued: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "paste_store", dict(self.paste_store))
        object.__setattr__(
            self,
            "client_message_id",
            str(self.client_message_id or "").strip() or short_uid(16),
        )
        object.__setattr__(
            self,
            "attachments",
            tuple(copy.deepcopy(item) for item in self.attachments),
        )
        object.__setattr__(self, "extras", copy.deepcopy(dict(self.extras)))

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

    @property
    def can_rollback(self) -> bool:
        """返回队尾消息是否仍可安全撤回编辑。"""
        return bool(self._items and not self._items[-1].server_queued)

    @property
    def waiting_settlement(self) -> bool:
        """返回是否仍有服务端持有的待结算消息。"""
        return any(item.server_queued for item in self._items)

    def append(self, item: TuiSubmission) -> None:
        """在队尾追加一条待提交消息。"""
        self._items.append(item)

    def append_next(self, item: TuiSubmission) -> None:
        """把服务端结算输入放到下一次读取位置。"""
        self._items.appendleft(item)

    def pop_next(self) -> TuiSubmission | None:
        """取出下一条应交给会话循环的消息。"""
        if not self._items or self._items[0].server_queued:
            return None
        return self._items.popleft()

    def pop_last(self) -> TuiSubmission | None:
        """撤回最近一条待提交消息。"""
        if not self.can_rollback:
            return None
        return self._items.pop()

    def remove(self, client_message_id: str) -> TuiSubmission | None:
        """按稳定消息标识移除一条待提交消息。"""
        for item in self._items:
            if item.client_message_id != client_message_id:
                continue
            self._items.remove(item)
            return item
        return None

    def release(self, client_message_id: str) -> bool:
        """解除一条未被服务端持有消息的撤回限制。"""
        for index, item in enumerate(self._items):
            if item.client_message_id != client_message_id:
                continue
            if item.server_queued:
                self._items[index] = replace(item, server_queued=False)
            return True
        return False

    def fragments(self, *, width: int, max_rows: int = 6) -> FormattedText:
        """生成动画行下方的待提交消息列表。"""
        if not self._items:
            return []

        row_limit = max(1, int(max_rows))

        lines: list[FormattedText] = [[(
            "class:queue.label",
            _queue_title(width, editable=self.can_rollback),
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


def _queue_title(width: int, *, editable: bool) -> str:
    """返回适合当前终端宽度的队列标题。"""
    titles = (
        (
            "• Messages queued for the next turn (Esc edits latest)",
            "• Queued for next turn (Esc edits latest)",
            "• Queued for next turn",
        )
        if editable
        else ("• Messages queued for the next turn", "• Queued for next turn")
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
