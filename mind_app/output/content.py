# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class AssistantTextDelta(object):
    """描述 assistant 正文的增量内容。"""

    text: str


@dataclass(frozen=True, slots=True)
class SourcesOutput(object):
    """描述当前回合引用的原始来源。"""

    sources: tuple[typing.Any, ...]


ContentOutput: typing.TypeAlias = AssistantTextDelta | SourcesOutput


class ContentSink(typing.Protocol):
    """接收与具体输出模式无关的正文内容。"""

    async def emit(self, output: ContentOutput) -> None:
        """发送一项结构化正文内容。"""
        ...


if __name__ == '__main__':
    pass
