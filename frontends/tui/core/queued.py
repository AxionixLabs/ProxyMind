# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import collections
import copy
import enum
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


class SteerDeliveryState(enum.Enum):
    """描述一条当前轮次输入的客户端交付状态。"""

    LOCAL = "local"
    SENT = "sent"
    UNCERTAIN = "uncertain"


@dataclass(frozen=True, slots=True)
class SteerResolution(object):
    """描述远端轮次边界对本地输入产生的恢复决议。"""

    retry: tuple["TuiSubmission", ...]
    uncertain: tuple["TuiSubmission", ...]
    resolved_ids: tuple[str, ...]


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

    def contains(self, client_message_id: str) -> bool:
        """返回稳定消息标识是否已存在于队列。"""
        return any(
            item.client_message_id == client_message_id
            for item in self._items
        )

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

    def drain(self) -> tuple[TuiSubmission, ...]:
        """按提交顺序取出全部待提交消息。"""
        items = tuple(self._items)
        self._items.clear()
        return items

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


class TuiRejectedSteers(object):
    """保存当前轮次未消费、将在轮末优先重投的输入。"""

    def __init__(self) -> None:
        self._items: collections.deque[TuiSubmission] = collections.deque()

    @property
    def active(self) -> bool:
        """返回当前是否存在等待轮末重投的输入。"""
        return bool(self._items)

    def append(self, item: TuiSubmission) -> None:
        """在队尾追加一条等待轮末重投的输入。"""
        self._items.append(item)

    def contains(self, client_message_id: str) -> bool:
        """返回稳定消息标识是否已存在于重投队列。"""
        return any(
            item.client_message_id == client_message_id
            for item in self._items
        )

    def prepend(self, item: TuiSubmission) -> None:
        """把显式中断后应立即提交的输入放到重投队首。"""
        self._items.appendleft(item)

    def pop_next(self) -> TuiSubmission | None:
        """取出下一条应当优先重投的输入。"""
        if not self._items:
            return None
        return self._items.popleft()

    def pop_last(self) -> TuiSubmission | None:
        """撤回最近一条等待轮末重投的输入。"""
        if not self._items:
            return None
        return self._items.pop()

    def drain(self) -> tuple[TuiSubmission, ...]:
        """按提交顺序取出全部被拒绝的即时输入。"""
        items = tuple(self._items)
        self._items.clear()
        return items

    def remove(self, client_message_id: str) -> TuiSubmission | None:
        """按稳定消息标识移除一条轮末重投输入。"""
        for item in self._items:
            if item.client_message_id != client_message_id:
                continue
            self._items.remove(item)
            return item
        return None

    def fragments(self, *, width: int, max_rows: int = 6) -> FormattedText:
        """生成等待轮末重投的消息列表。"""
        if not self._items:
            return []

        row_limit = max(1, int(max_rows))
        lines: list[FormattedText] = [_rejected_steer_title(width)]
        lines.extend(_submission_lines(
            self._items,
            available=max(0, row_limit - 1),
            width=width,
        ))
        return _join_lines(lines, width=width)


class TuiPendingSteers(object):
    """保存当前轮次输入及其唯一客户端交付状态。"""

    def __init__(self) -> None:
        self._items: dict[
            str,
            tuple[TuiSubmission, SteerDeliveryState],
        ] = {}
        self._interrupt_settling = False

    @property
    def active(self) -> bool:
        """返回当前是否存在等待提交的输入。"""
        return bool(self._items)

    @property
    def uncertain_active(self) -> bool:
        """返回当前是否存在归属未确认的输入。"""
        return any(
            state is SteerDeliveryState.UNCERTAIN
            for _, state in self._items.values()
        )

    @property
    def active_ids(self) -> tuple[str, ...]:
        """返回尚未收到权威归属的当前轮次输入标识。"""
        return tuple(
            client_message_id
            for client_message_id, (_, state) in self._items.items()
            if state is not SteerDeliveryState.UNCERTAIN
        )

    def contains(self, client_message_id: str) -> bool:
        """返回稳定消息标识是否存在于当前轮次账本。"""
        return client_message_id in self._items

    def add(self, item: TuiSubmission) -> None:
        """记录一条等待当前轮次接收的输入。"""
        if not self.active_ids:
            self._interrupt_settling = False
        self._items.setdefault(
            item.client_message_id,
            (item, SteerDeliveryState.LOCAL),
        )

    def next_local(
        self,
        client_message_ids: tuple[str, ...],
    ) -> TuiSubmission | None:
        """返回指定轮次最早一条尚未发送的输入。"""
        for client_message_id in client_message_ids:
            item = self._items.get(client_message_id)
            if item is None:
                continue
            submission, state = item
            if state is SteerDeliveryState.LOCAL:
                return submission
        return None

    def mark_sent(self, client_message_id: str) -> None:
        """把本地输入原子转换为已发送待确认状态。"""
        item = self._items.get(client_message_id)
        if item is None:
            return None
        submission, state = item
        if state is SteerDeliveryState.LOCAL:
            self._items[client_message_id] = (
                submission,
                SteerDeliveryState.SENT,
            )

    def sent_ids(
        self,
        client_message_ids: tuple[str, ...],
    ) -> tuple[str, ...]:
        """返回指定轮次需要远端对账的已发送输入标识。"""
        return tuple(
            client_message_id
            for client_message_id in client_message_ids
            if (
                (item := self._items.get(client_message_id)) is not None
                and item[1] is SteerDeliveryState.SENT
            )
        )

    def remove(self, client_message_id: str) -> TuiSubmission | None:
        """移除一条已经确认或转入下一轮的输入。"""
        item = self._items.pop(client_message_id, None)
        if not self.active_ids:
            self._interrupt_settling = False
        return item[0] if item is not None else None

    def retain_uncertain(self, item: TuiSubmission) -> None:
        """保留一条不得自动重试的未确认输入。"""
        self._items[item.client_message_id] = (
            item,
            SteerDeliveryState.UNCERTAIN,
        )
        self._interrupt_settling = False

    def mark_interrupt_settling(self) -> bool:
        """把未确认即时输入投影为等待中断结算的下一轮候选。"""
        if not self.active_ids or self._interrupt_settling:
            return False
        self._interrupt_settling = True
        return True

    def pop_last_uncertain(self) -> TuiSubmission | None:
        """取回最近一条归属未确认的输入。"""
        for client_message_id in reversed(self._items):
            submission, state = self._items[client_message_id]
            if state is SteerDeliveryState.UNCERTAIN:
                self._items.pop(client_message_id)
                return submission
        return None

    def drain(self) -> tuple[TuiSubmission, ...]:
        """按登记顺序取出全部未确认输入。"""
        items = tuple(submission for submission, _ in self._items.values())
        self._items.clear()
        self._interrupt_settling = False
        return items

    def advance(
        self,
        client_message_ids: tuple[str, ...],
        *,
        settled: bool,
    ) -> SteerResolution:
        """进入 continuation，并裁决上一远端 Turn 的已发送输入。"""
        uncertain: list[TuiSubmission] = []
        resolved_ids: list[str] = []
        for client_message_id in client_message_ids:
            item = self._items.get(client_message_id)
            if item is None:
                resolved_ids.append(client_message_id)
                continue
            submission, state = item
            if state is SteerDeliveryState.LOCAL:
                continue
            self._items.pop(client_message_id)
            resolved_ids.append(client_message_id)
            if not settled and state is SteerDeliveryState.SENT:
                uncertain.append(submission)
        if not self.active_ids:
            self._interrupt_settling = False
        return SteerResolution(
            retry=(),
            uncertain=tuple(uncertain),
            resolved_ids=tuple(resolved_ids),
        )

    def close(
        self,
        client_message_ids: tuple[str, ...],
        *,
        settled: bool,
        committed_ids: tuple[str, ...] = (),
        retry_ids: tuple[str, ...] = (),
    ) -> SteerResolution:
        """关闭当前轮次账本并返回输入恢复决议。"""
        committed = set(committed_ids)
        retry = set(retry_ids)
        retry_items: list[TuiSubmission] = []
        uncertain_items: list[TuiSubmission] = []
        resolved_ids: list[str] = []

        for client_message_id in client_message_ids:
            item = self._items.pop(client_message_id, None)
            if item is None:
                continue
            submission, state = item
            resolved_ids.append(client_message_id)
            if client_message_id in committed:
                continue
            if (
                state is SteerDeliveryState.LOCAL
                or client_message_id in retry
                or settled
            ):
                retry_items.append(submission)
            else:
                uncertain_items.append(submission)

        if not self.active_ids:
            self._interrupt_settling = False
        return SteerResolution(
            retry=tuple(retry_items),
            uncertain=tuple(uncertain_items),
            resolved_ids=tuple(resolved_ids),
        )

    def fragments(self, *, width: int, max_rows: int = 6) -> FormattedText:
        """生成等待当前轮次接收的消息列表。"""
        if not self.active:
            return []

        row_limit = max(1, int(max_rows))

        lines: list[FormattedText] = []

        pending_items = tuple(
            submission
            for submission, state in self._items.values()
            if state is not SteerDeliveryState.UNCERTAIN
        )
        uncertain_items = tuple(
            submission
            for submission, state in self._items.values()
            if state is SteerDeliveryState.UNCERTAIN
        )

        if pending_items:
            lines.append(
                _interrupt_settling_title(width)
                if self._interrupt_settling
                else _pending_steer_title(width)
            )
            lines.extend(_submission_lines(
                pending_items,
                available=max(0, row_limit - len(lines)),
                width=width,
            ))

        if uncertain_items and len(lines) < row_limit:
            lines.append([("class:queue.label", "• Delivery unconfirmed")])
            lines.extend(_submission_lines(
                uncertain_items,
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


def _interrupt_settling_title(width: int) -> FormattedText:
    """返回等待中断轮次完成对账的输入标题。"""
    titles = (
        "Queued while interrupted turn settles",
        "Queued while settling",
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


def _rejected_steer_title(width: int) -> FormattedText:
    """返回适合当前终端宽度的轮末重投标题。"""
    titles = (
        "Messages to be submitted at end of turn",
        "Submit at end of turn",
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
