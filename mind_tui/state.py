# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

from dataclasses import (
    dataclass
)


@dataclass(frozen=True, slots=True)
class QueuedMessage:
    """保存一条等待提交的输入消息。"""

    text: str
    shell_mode: bool = False


class TuiState:
    """保存独立终端界面的可变展示状态。"""

    def __init__(self, transcript: str) -> None:
        """初始化界面显示和输入队列状态。"""
        self.transcript = transcript
        self.status_text = ""
        self.status_phase = 0
        self.shell_mode = False
        self.follow_tail = True
        self.approval_visible = False
        self.busy = False
        self.submitted: list[str] = []
        self.queued_messages: list[QueuedMessage] = []

    def append(self, text: str) -> None:
        """向会话正文追加文本。"""
        self.transcript += str(text or "")

    def set_status(self, text: str) -> None:
        """设置固定状态行并重置动画相位。"""
        self.status_text = str(text or "").strip()
        self.status_phase = 0

    def tick_status(self) -> None:
        """推进固定状态行的动画相位。"""
        self.status_phase += 1

    def enqueue(self, text: str, *, shell_mode: bool = False) -> QueuedMessage:
        """把输入追加到等待提交队列。"""
        item = QueuedMessage(str(text or "").strip(), shell_mode=shell_mode)
        self.queued_messages.append(item)
        return item

    def dequeue(self) -> QueuedMessage | None:
        """按先进先出顺序取出下一条等待消息。"""
        if not self.queued_messages:
            return None
        return self.queued_messages.pop(0)

    def rollback_queue(self) -> QueuedMessage | None:
        """把最近入队的消息撤回输入区。"""
        if not self.queued_messages:
            return None
        return self.queued_messages.pop()


def consume_shell_prefix(
    text: str,
    cursor_position: int,
    *,
    shell_mode: bool
) -> tuple[str, int, bool]:
    """消费输入开头的叹号并返回新的输入状态。"""
    if shell_mode or not text.startswith("!"):
        return text, cursor_position, shell_mode

    return text[1:], max(0, cursor_position - 1), True
