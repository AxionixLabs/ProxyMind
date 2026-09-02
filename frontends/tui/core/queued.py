# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import collections
import copy
import typing
from dataclasses import (
    dataclass,
    field,
)

from prompt_toolkit.utils import get_cwidth

from frontends.terminal.text import sanitize_terminal_text
from protocol.schema.identifiers import short_uid
from .models import FormattedText
from ..rendering.fragments import (
    clip_fragments,
    wrap_formatted_lines
)

PREVIEW_LINE_LIMIT = 3


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

    @property
    def literal_bang_paste(self) -> bool:
        """判断普通输入展开后以感叹号开头的粘贴文本。"""
        return bool(
            not self.shell_mode
            and self.paste_store
            and not self.editable_text.lstrip().startswith("!")
            and self.value.lstrip().startswith("!")
        )


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

        lines: list[FormattedText] = [_queue_title(width)]

        binding = str(edit_binding or "").strip()
        lines.extend(_submission_lines(
            self._items,
            available=max(0, row_limit - 1 - int(bool(binding))),
            width=width,
            text_style="class:queue.text.queued",
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
        self._items: dict[str, TuiSubmission] = {}
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
                width=width,
            ))

        if self._uncertain and len(lines) < row_limit:
            lines.append([("class:queue.label", "• Delivery unconfirmed")])
            lines.extend(_submission_lines(
                self._uncertain.values(),
                available=max(0, row_limit - len(lines)),
                width=width,
            ))

        return _join_lines(lines, width=width)


def _queue_title(width: int) -> FormattedText:
    """返回适合当前终端宽度的队列标题。"""
    titles = (
        "Queued follow-up inputs",
        "Queued inputs",
    )

    limit = max(1, int(width))

    for title in titles:
        if get_cwidth(f"• {title}") <= limit:
            return [
                ("class:queue.marker", "• "),
                ("class:queue.label", title),
            ]

    return [
        ("class:queue.marker", "• "),
        ("class:queue.label", titles[-1]),
    ]


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
    width: int,
    text_style: str = "class:queue.text"
) -> list[FormattedText]:
    """按可用行数生成保留换行的消息预览和隐藏数量。"""
    if available <= 0:
        return []

    submissions = tuple(items)

    lines: list[FormattedText] = []

    visible_count: int = 0

    for index, item in enumerate(submissions):
        remaining = available - len(lines)
        if remaining <= 0:
            break

        preview_lines = _submission_preview_lines(
            item,
            width=width,
            text_style=text_style,
        )

        has_later_messages = index < len(submissions) - 1

        preview_budget = (
            max(0, remaining - 1)
            if has_later_messages and len(preview_lines) >= remaining
            else remaining
        )
        if preview_budget <= 0:
            break

        lines.extend(_limit_preview_lines(
            preview_lines,
            available=preview_budget,
            text_style=text_style,
        ))
        visible_count += 1

        if len(preview_lines) > preview_budget:
            break

    hidden_count = len(submissions) - visible_count
    if hidden_count and len(lines) < available:
        lines.append([(
            "class:queue.more",
            f"    … {hidden_count} more",
        )])
    return lines


def _submission_preview_lines(
    item: TuiSubmission,
    *,
    width: int,
    text_style: str
) -> list[FormattedText]:
    """生成一条消息的终端折行预览。"""
    content_width = max(1, int(width) - get_cwidth("  ↳ "))

    text = sanitize_terminal_text(item.value or item.visible_text)

    wrapped = wrap_formatted_lines(
        [(text_style, text)],
        width=content_width,
    ) or [[(text_style, "")]]

    lines: list[FormattedText] = []

    for index, line in enumerate(wrapped[:PREVIEW_LINE_LIMIT]):
        lines.append([
            (
                "class:queue.marker",
                "  ↳ " if index == 0 else "    ",
            ),
            *line,
        ])

    if len(wrapped) > PREVIEW_LINE_LIMIT:
        lines.append([(text_style, "    …")])
    return lines


def _limit_preview_lines(
    lines: list[FormattedText],
    *,
    available: int,
    text_style: str
) -> list[FormattedText]:
    """把单条预览限制到行预算并保留溢出标记。"""
    if len(lines) <= available:
        return lines
    if available <= 1:
        return lines[:available]
    return [
        *lines[:available - 1],
        [(text_style, "    …")],
    ]


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
