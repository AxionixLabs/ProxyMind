# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class AssistantTextDelta(object):
    """描述 assistant 正文的增量内容。"""

    text: str


@dataclass(frozen=True, slots=True)
class AssistantSegmentCompleted(object):
    """描述一段 assistant 正文已经完成。"""


@dataclass(frozen=True, slots=True)
class AssistantOutputBoundary(object):
    """描述 assistant 正文与后续结构化输出之间的边界。"""


@dataclass(frozen=True, slots=True)
class AssistantPresentationSuperseded(object):
    """描述旧 Worker 展示代次退出规范输出。"""
    superseded_epoch: int
    presentation_epoch: int


@dataclass(frozen=True, slots=True)
class AssistantResponseSuperseded(object):
    """描述当前模型 round 的旧 provider attempt 退出规范输出。"""
    presentation_epoch: int
    round: int
    attempt: int


@dataclass(frozen=True, slots=True)
class SourcesOutput(object):
    """描述当前回合引用的原始来源。"""

    sources: tuple[typing.Any, ...]


ContentOutput: typing.TypeAlias = (
    AssistantTextDelta
    | AssistantSegmentCompleted
    | AssistantOutputBoundary
    | AssistantPresentationSuperseded
    | AssistantResponseSuperseded
    | SourcesOutput
)


class ContentSink(typing.Protocol):
    """接收与具体输出模式无关的正文内容。"""

    async def emit(self, output: ContentOutput) -> None:
        """发送一项结构化正文内容。"""
        ...


if __name__ == '__main__':
    pass
