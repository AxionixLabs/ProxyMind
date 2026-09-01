# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
from abc import (
    ABC,
    abstractmethod
)

TranscriptActor: typing.TypeAlias = typing.Literal[
    "user",
    "assistant",
    "system",
    "tool",
]


class TranscriptSink(ABC):
    """定义结构化会话事件的追加写入能力。"""

    @abstractmethod
    def append(
        self,
        event: str,
        *,
        actor: TranscriptActor | None = None,
        payload: dict[str, typing.Any] | None = None
    ) -> None:
        """追加一个结构化事件。"""
        ...


class TranscriptFactory(typing.Protocol):
    """定义按会话和轮次创建 Transcript 写入器的端口。"""

    def __call__(
        self,
        path: str,
        *,
        session_id: str,
        turn_id: str | None = None,
    ) -> TranscriptSink:
        """创建绑定固定会话坐标的 Transcript 写入器。"""
        ...


if __name__ == '__main__':
    pass
