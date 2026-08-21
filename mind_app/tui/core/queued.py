# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import copy
import typing
import collections
from dataclasses import (
    dataclass,
    field
)
from prompt_toolkit.utils import get_cwidth
from mind_nova.identifiers import short_uid
from mind_app.presentation.terminal_text import sanitize_terminal_text
from .models import FormattedText
from ..rendering.fragments import clip_fragments


@dataclass(frozen=True, slots=True)
class TuiSubmission(object):
    """保存一次输入提交的请求文本和可见编辑状态。"""
    value: str
    editable_text: str
    paste_store: dict[str, str]
    shell_mode: bool = False
    history_recorded: bool = False
    client_message_id: str = field(default_factory=lambda: short_uid(16))
    attachments: tuple[dict[str, typing.Any], ...] = ()
    extras: dict[str, typing.Any] = field(default_factory=dict)
    payload_bound: bool = False

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
    """管理执行期间由用户主动排队的可编辑输入。"""

    def __init__(self) -> None:
        self._items: collections.deque[TuiSubmission] = collections.deque()

    @property
    def active(self) -> bool:
        """返回当前是否存在待提交消息。"""
        return bool(self._items)

    @property
    def can_rollback(self) -> bool:
        """返回是否可以取回队尾消息继续编辑。"""
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

    def fragments(
        self,
        *,
        width: int,
        max_rows: int = 6,
        edit_binding: str = "alt + ↑"
    ) -> FormattedText:
        """生成动画行下方的待提交消息列表。"""
        if not self._items:
            return []

        row_limit = max(1, int(max_rows))

        lines: list[FormattedText] = [[(
            "class:queue.label",
            _queue_title(width),
        )]]

        binding = str(edit_binding or "").strip()
        lines.extend(_submission_lines(
            self._items,
            available=max(0, row_limit - 1 - int(bool(binding))),
        ))
        if binding and len(lines) < row_limit:
            lines.append([(
                "class:queue.edit-hint",
                f"    {binding} edit last queued message",
            )])

        return _join_lines(lines, width=width)


class TuiPendingSteers(object):
    """保存等待写入当前执行轮次的输入。"""

    def __init__(self) -> None:
        self._items: dict[str, TuiSubmission]     = {}
        self._uncertain: dict[str, TuiSubmission] = {}

    @property
    def active(self) -> bool:
        """返回当前是否存在等待提交的输入。"""
        return bool(self._items or self._uncertain)

    @property
    def uncertain_active(self) -> bool:
        """返回当前是否存在归属未确认的输入。"""
        return bool(self._uncertain)

    def add(self, item: TuiSubmission) -> None:
        """记录一条等待当前轮次接收的输入。"""
        self._uncertain.pop(item.client_message_id, None)
        self._items[item.client_message_id] = item

    def remove(self, client_message_id: str) -> TuiSubmission | None:
        """移除一条已经确认或转入下一轮的输入。"""
        return (
            self._items.pop(client_message_id, None)
            or self._uncertain.pop(client_message_id, None)
        )

    def retain_uncertain(self, item: TuiSubmission) -> None:
        """保留一条不得自动重试的未确认输入。"""
        self._items.pop(item.client_message_id, None)
        self._uncertain[item.client_message_id] = item

    def pop_last_uncertain(self) -> TuiSubmission | None:
        """取回最近一条归属未确认的输入。"""
        if not self._uncertain:
            return None
        client_message_id = next(reversed(self._uncertain))
        return self._uncertain.pop(client_message_id)

    def fragments(self, *, width: int, max_rows: int = 6) -> FormattedText:
        """生成等待当前轮次接收的消息列表。"""
        if not self.active:
            return []

        row_limit = max(1, int(max_rows))

        lines: list[FormattedText] = []

        if self._items:
            lines.append(_pending_steer_title(width))
            lines.extend(_submission_lines(
                self._items.values(),
                available=max(0, row_limit - len(lines)),
            ))

        if self._uncertain and len(lines) < row_limit:
            lines.append([("class:queue.label", "• Delivery unconfirmed")])
            lines.extend(_submission_lines(
                self._uncertain.values(),
                available=max(0, row_limit - len(lines)),
            ))

        return _join_lines(lines, width=width)


def _queue_title(width: int) -> str:
    """返回适合当前终端宽度的队列标题。"""
    titles = (
        "• Queued follow-up inputs",
        "• Queued inputs",
    )

    limit = max(1, int(width))

    for title in titles:
        if get_cwidth(title) <= limit:
            return title

    return titles[-1]


def _pending_steer_title(width: int) -> FormattedText:
    """返回适合当前终端宽度的即时输入标题。"""
    titles = (
        (
            "• Messages to be submitted after next tool call",
            " (press ctrl + c to interrupt and send immediately)",
        ),
        ("• Messages to be submitted after next tool call", ""),
        ("• Submit after next tool call", ""),
    )
    limit = max(1, int(width))
    for label, hint in titles:
        if get_cwidth(f"{label}{hint}") <= limit:
            fragments = [("class:queue.label", label)]
            if hint:
                fragments.append(("class:queue.hint", hint))
            return fragments

    return [("class:queue.label", titles[-1][0])]


def _submission_lines(
    items: typing.Iterable[TuiSubmission],
    *,
    available: int,
) -> list[FormattedText]:
    """按可用行数生成消息预览和隐藏数量。"""
    if available <= 0:
        return []

    submissions = tuple(items)
    visible_count = min(len(submissions), available)
    if len(submissions) > available:
        visible_count = max(0, available - 1)

    lines: list[FormattedText] = []
    for item in submissions[:visible_count]:
        preview = " ".join(sanitize_terminal_text(item.visible_text).split())
        lines.append([
            ("class:queue.marker", "  ↳ "),
            ("class:queue.text", preview),
        ])

    hidden_count = len(submissions) - visible_count
    if hidden_count:
        lines.append([(
            "class:queue.more",
            f"    … {hidden_count} more",
        )])
    return lines


def _join_lines(lines: list[FormattedText], *, width: int) -> FormattedText:
    """连接并裁剪多行格式化文本。"""
    out: FormattedText = []
    for index, line in enumerate(lines):
        if index:
            out.append(("", "\n"))
        out.extend(_ellipsize_fragments(line, width=max(1, width)))
    return out


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
