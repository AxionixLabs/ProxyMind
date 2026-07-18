# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

from dataclasses import dataclass

from .bottom_pane import BottomPaneState
from .transcript import TranscriptState


@dataclass(frozen=True, slots=True)
class QueuedMessage:
    """保存一条等待提交的输入消息。"""

    text: str
    shell_mode: bool = False


@dataclass(slots=True)
class StatusState:
    """保存状态区文本和动画相位。"""

    text: str = ""
    phase: int = 0

    def set(self, text: str) -> None:
        """更新状态文本并重置动画相位。"""
        self.text = str(text or "").strip()
        self.phase = 0

    def tick(self) -> None:
        """推进状态动画相位。"""
        self.phase += 1


class TurnState:
    """保存当前轮次和等待提交队列。"""

    def __init__(self) -> None:
        """初始化轮次状态和消息队列。"""
        self.busy = False
        self.submitted: list[str] = []
        self.queued_messages: list[QueuedMessage] = []

    def enqueue(self, text: str, *, shell_mode: bool = False) -> QueuedMessage:
        """把输入追加到等待提交队列。"""
        item = QueuedMessage(str(text or "").strip(), shell_mode=shell_mode)
        self.queued_messages.append(item)
        return item

    def dequeue(self) -> QueuedMessage | None:
        """按先进先出顺序取出下一条消息。"""
        if not self.queued_messages:
            return None
        return self.queued_messages.pop(0)

    def rollback(self) -> QueuedMessage | None:
        """撤回最近入队的消息。"""
        if not self.queued_messages:
            return None
        return self.queued_messages.pop()


@dataclass(slots=True)
class ComposerState:
    """保存输入区编辑模式。"""

    shell_mode: bool = False


@dataclass(slots=True)
class ViewportState:
    """保存会话视口的跟随状态。"""

    follow_tail: bool = True


class TuiState:
    """组合独立终端应用的可变状态。"""

    def __init__(self, transcript: str) -> None:
        """初始化会话、轮次、输入区和底部视图状态。"""
        self.transcript = TranscriptState(transcript)
        self.bottom_pane = BottomPaneState()
        self.status = StatusState()
        self.turn = TurnState()
        self.composer = ComposerState()
        self.viewport = ViewportState()


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
