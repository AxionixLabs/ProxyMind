# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
from dataclasses import (
    dataclass,
    field
)


@dataclass(frozen=True, slots=True)
class ResponseIdentity(object):
    """标识一个 Turn 内唯一的 provider response attempt。"""
    turn_id: str
    presentation_epoch: int
    round: int
    attempt: int

    def __post_init__(self) -> None:
        """拒绝无法稳定定位 assistant item 的响应身份。"""
        if not str(self.turn_id or "").strip():
            raise ValueError("response identity turn_id is required")
        for name in ("presentation_epoch", "round", "attempt"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                raise ValueError(f"response identity {name} must be a positive integer")

    def as_dict(self) -> dict[str, typing.Any]:
        """返回 JSONL assistant item 使用的扁平身份字段。"""
        return {
            "turn_id": self.turn_id,
            "presentation_epoch": self.presentation_epoch,
            "round": self.round,
            "attempt": self.attempt,
        }


@dataclass(frozen=True, slots=True)
class AssistantTextDelta(object):
    """描述 assistant 正文的增量内容。"""
    text: str
    identity: ResponseIdentity
    item_id: str = field(default="", compare=False)


@dataclass(frozen=True, slots=True)
class AssistantSegmentCompleted(object):
    """描述一段 assistant 正文已经完成。"""
    identity: ResponseIdentity
    final_text: str | None = field(default=None, compare=False)
    item_id: str = field(default="", compare=False)


@dataclass(frozen=True, slots=True)
class AssistantOutputBoundary(object):
    """描述 assistant 正文与后续结构化输出之间的边界。"""


@dataclass(frozen=True, slots=True)
class AssistantPresentationSuperseded(object):
    """描述旧 Worker 展示代次退出规范输出。"""
    turn_id: str
    superseded_epoch: int
    presentation_epoch: int


@dataclass(frozen=True, slots=True)
class AssistantResponseSuperseded(object):
    """描述当前模型 round 的旧 provider attempt 退出规范输出。"""
    turn_id: str
    presentation_epoch: int
    round: int
    attempt: int
    item_id: str = ""


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
