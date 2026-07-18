# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Protocol
from ..events import AppEvent


@dataclass(frozen=True, slots=True)
class TurnRequest:
    """表示提交给运行时的一轮输入。"""

    message: str
    shell_mode: bool = False


class StreamProvider(Protocol):
    """定义应用可消费的结构化事件来源。"""

    def stream(self, request: TurnRequest) -> AsyncIterator[AppEvent]:
        """返回一轮输入对应的异步事件流。"""
        ...


if __name__ == '__main__':
    pass
