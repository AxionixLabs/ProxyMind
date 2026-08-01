# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing

TranscriptActor: typing.TypeAlias = typing.Literal[
    "user",
    "assistant",
    "system",
    "tool",
]


class TranscriptSink(typing.Protocol):
    """定义结构化会话事件的追加写入能力。"""

    def append(
        self,
        event: str,
        *,
        actor: TranscriptActor | None = None,
        payload: dict[str, typing.Any] | None = None
    ) -> None:
        """追加一个结构化事件。"""
        ...


if __name__ == '__main__':
    pass
