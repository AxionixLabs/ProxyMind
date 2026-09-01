# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing

from agent.domain.transcripts import TranscriptActor


@typing.runtime_checkable
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


@typing.runtime_checkable
class TranscriptLifecyclePort(TranscriptSink, typing.Protocol):
    """定义单轮 Transcript 在完成追加后的关闭生命周期。"""

    def close(self) -> None:
        """幂等关闭当前 Transcript 写入生命周期。"""
        ...


@typing.runtime_checkable
class TranscriptSessionPort(TranscriptLifecyclePort, typing.Protocol):
    """定义绑定固定会话坐标的完整 Transcript 写入生命周期。"""

    def open(self) -> None:
        """幂等打开当前 Transcript 写入生命周期。"""
        ...


class TranscriptFactory(typing.Protocol):
    """定义按会话和轮次创建 Transcript 写入器的端口。"""

    def __call__(
        self,
        path: str,
        *,
        session_id: str,
        turn_id: str | None = None,
    ) -> TranscriptSessionPort:
        """创建绑定固定会话坐标的 Transcript 写入器。"""
        ...


if __name__ == '__main__':
    pass
