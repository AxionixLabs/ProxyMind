# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import asyncio
import typing

from frontends.terminal.text import (
    sanitize_terminal_line,
    sanitize_terminal_text,
)
from .models import (
    FormattedText,
    MailboxEntry,
)
from ..rendering.fragments import (
    clip_text,
    join_formatted_lines,
    wrap_formatted_lines,
)

MAILBOX_COUNT_DISPLAY_LIMIT: typing.Final[int] = 999


def format_mailbox_count(count: int) -> str:
    """把消息数量格式化为有界且稳定的展示文本。"""
    normalized = max(0, int(count))
    if normalized > MAILBOX_COUNT_DISPLAY_LIMIT:
        return f"{MAILBOX_COUNT_DISPLAY_LIMIT}+"
    return str(normalized)


class TuiMailboxOverlay(object):
    """管理全屏只读消息详情的快照、关闭等待和正文翻页。"""

    def __init__(
        self,
        *,
        get_width: typing.Callable[[], int],
        get_height: typing.Callable[[], int],
        invalidate: typing.Callable[[], None]
    ) -> None:
        self._get_width = get_width
        self._get_height = get_height
        self._invalidate = invalidate
        self.active: bool = False
        self.listener_active: bool = False
        self.entries: tuple[MailboxEntry, ...] = ()
        self.selected_key: str = ""
        self.message_offset: int = 0

        self._closed_future: asyncio.Future[None] | None = None

    @property
    def pending_count(self) -> int:
        """返回当前快照中的待处理消息数量。"""
        return len(self.entries)

    @property
    def selected_entry(self) -> MailboxEntry | None:
        """返回当前选中的消息。"""
        return next(
            (
                entry
                for entry in self.entries
                if entry.key == self.selected_key
            ),
            None,
        )

    @staticmethod
    def _sanitize_entry(entry: MailboxEntry) -> MailboxEntry:
        """过滤一条消息中的终端控制字符。"""
        key = str(entry.key)
        title = sanitize_terminal_line(entry.title)
        message = sanitize_terminal_text(entry.message)
        detail = sanitize_terminal_line(entry.detail)

        return MailboxEntry(
            key=key,
            title=title or "Untitled message",
            message=message if message.strip() else "(empty message)",
            detail=detail,
        )

    def _message_height(self) -> int:
        """返回扣除消息摘要后的正文可用高度。"""
        height = max(0, self._get_height())
        return max(0, height - 4)

    def _message_rows(self) -> list[FormattedText]:
        """按当前终端宽度折行选中消息正文。"""
        entry = self.selected_entry
        if entry is None:
            return []

        width = max(1, self._get_width() - 2)

        rows = wrap_formatted_lines(
            [("class:mailbox.message", entry.message)],
            width=width,
        )

        return [
            [("class:mailbox.message", "  "), *row]
            for row in rows
        ]

    def update(
        self,
        entries: typing.Iterable[MailboxEntry],
        *,
        listener_active: bool
    ) -> bool:
        """替换只读消息快照并尽量保留当前选择。"""
        previous = self.selected_entry
        previous_by_key = {entry.key: entry for entry in self.entries}

        normalized = tuple(
            previous_by_key.get(entry.key) or self._sanitize_entry(entry)
            for entry in entries
        )

        active = bool(listener_active)

        if normalized == self.entries and active == self.listener_active:
            return False

        self.entries = normalized
        self.listener_active = active

        selected = self.selected_entry

        if (
            previous is None
            or selected is None
            or previous.key != selected.key
        ):
            self.message_offset = 0

        if self.active:
            self._invalidate()
        return True

    def open(self, entry_key: str) -> bool:
        """打开指定消息的全屏详情并建立关闭等待。"""
        if self.active:
            raise RuntimeError("mailbox detail is already active")
        if not any(entry.key == entry_key for entry in self.entries):
            return False

        self.active = True
        self.selected_key = entry_key
        self.message_offset = 0

        self._closed_future = asyncio.get_running_loop().create_future()
        self._invalidate()

        return True

    def close(self) -> None:
        """关闭消息详情并完成当前等待。"""
        future = self._closed_future

        self.active = False
        self.selected_key = ""
        self.message_offset = 0

        if future is not None and not future.done():
            future.set_result(None)
        self._invalidate()

    def abort(self) -> None:
        """在全屏切换失败时丢弃详情状态且不触发额外绘制。"""
        future = self._closed_future

        self.active = False
        self.selected_key = ""
        self.message_offset = 0

        if future is not None and not future.done():
            future.set_result(None)

    def scroll_lines(self, step: int) -> None:
        """按给定物理行数滚动消息正文。"""
        rows = self._message_rows()

        available = self._message_height()
        if available <= 0:
            return None

        maximum = max(0, len(rows) - available)

        target = max(0, min(maximum, self.message_offset + step))
        if target == self.message_offset:
            return None
        self.message_offset = target
        self._invalidate()

    def scroll_page(self, direction: int) -> None:
        """按当前正文视口高度翻动消息正文。"""
        self.scroll_lines(direction * max(1, self._message_height()))

    def jump_message(self, *, to_end: bool) -> None:
        """跳转到消息正文开头或末尾。"""
        maximum = max(0, len(self._message_rows()) - self._message_height())

        target = maximum if to_end else 0
        if target == self.message_offset:
            return None
        self.message_offset = target
        self._invalidate()

    def visible_fragments(self) -> FormattedText:
        """生成当前正文窗口可见的单条消息详情。"""
        height = max(0, self._get_height())
        if height <= 0:
            return []

        entry = self.selected_entry
        if entry is None:
            lines: list[FormattedText] = [
                [],
                [("class:mailbox.empty", "  Message is no longer available.")],
            ]
            return join_formatted_lines(lines[:height])

        width = max(1, self._get_width())
        detail = entry.detail or sanitize_terminal_line(entry.key)

        lines = [
            [],
            [
                ("class:mailbox.subject", "  "),
                (
                    "class:mailbox.subject",
                    clip_text(entry.title, width=max(1, width - 2)),
                ),
            ],
            [
                ("class:mailbox.detail", "  "),
                (
                    "class:mailbox.detail",
                    clip_text(detail, width=max(1, width - 2)),
                ),
            ],
            [],
        ][:height]

        available = max(0, height - len(lines))
        message_rows = self._message_rows()
        maximum = max(0, len(message_rows) - available)

        self.message_offset = min(self.message_offset, maximum)

        lines.extend(
            message_rows[
                self.message_offset:self.message_offset + available
            ]
        )

        return join_formatted_lines(lines[:height])

    def message_progress(self) -> tuple[int, int]:
        """返回选中消息正文的当前页和总页数。"""
        rows = self._message_rows()
        height = self._message_height()

        if not rows or height <= 0:
            return 0, 0

        total = max(1, (len(rows) + height - 1) // height)
        maximum = max(0, len(rows) - height)
        offset = min(self.message_offset, maximum)
        current = min(total, (offset + height - 1) // height + 1)

        return current, total

    async def wait_closed(self) -> None:
        """等待当前全屏消息详情关闭。"""
        future = self._closed_future
        if future is None:
            raise RuntimeError("mailbox detail is not active")
        await future


if __name__ == '__main__':
    pass
