# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing

from agent.application.turns.run_result import RunStatus
from agent.application.turns.stream_outcome import StreamTurnOutcome
from agent.protocol import CanonicalItem
from protocol.schema.stream_events import StreamEvent


@typing.runtime_checkable
class TurnEventProjection(typing.Protocol):
    """定义共享 Turn 事件泵可插入的命令级展示策略。

    实现方只消费所属命令的专用事件，并从权威终态生成最终 assistant 文本；
    工具、活动、重试和 Turn 终态仍由共享事件泵拥有。
    """

    @property
    def assistant_output_visible(self) -> bool:
        """返回普通 assistant text Item 是否允许上屏和写入 Transcript。"""
        ...

    @property
    def run_lifecycle_visible(self) -> bool:
        """返回普通 Turn 启动、失败和完成视图是否可见。"""
        ...

    async def observe(
        self,
        event: StreamEvent,
        current_item: CanonicalItem | None,
    ) -> bool:
        """投影一项命令专用事件，并返回是否已完整消费。"""
        ...

    async def failure(
        self,
        status: RunStatus,
        error: str,
        *,
        effect_id: str = "",
    ) -> None:
        """在共享事件泵产生本地失败时投影命令级错误。"""
        ...

    def assistant_text(self, fallback: str) -> str:
        """返回命令权威结果对应的最终 assistant 文本。"""
        ...


class TurnProjectionCoordinator:
    """统一共享事件泵的可选命令投影与默认展示语义。"""

    def __init__(self, projection: TurnEventProjection | None) -> None:
        """校验并保存可选的命令级投影。"""
        if projection is not None and not isinstance(
            projection,
            TurnEventProjection,
        ):
            raise TypeError("turn event projection is invalid")
        self._projection = projection

    @property
    def assistant_output_visible(self) -> bool:
        """返回普通 assistant 输出是否可见。"""
        projection = self._projection
        return projection is None or projection.assistant_output_visible

    @property
    def run_lifecycle_visible(self) -> bool:
        """返回普通 Run 生命周期是否可见。"""
        projection = self._projection
        return projection is None or projection.run_lifecycle_visible

    async def observe(
        self,
        event: StreamEvent,
        current_item: CanonicalItem | None,
    ) -> bool:
        """把事件交给可选命令投影。"""
        projection = self._projection
        return bool(
            projection is not None
            and await projection.observe(event, current_item)
        )

    async def failure(
        self,
        outcome: StreamTurnOutcome,
        *,
        effect_id: str = "",
    ) -> None:
        """把共享事件泵失败交给可选命令投影。"""
        projection = self._projection
        if projection is None:
            return None
        await projection.failure(
            outcome.status,
            outcome.error or "Turn failed.",
            effect_id=effect_id,
        )

    def assistant_text(self, fallback: str) -> str:
        """返回命令投影拥有的最终 assistant 文本。"""
        projection = self._projection
        return fallback if projection is None else projection.assistant_text(fallback)


if __name__ == '__main__':
    pass
