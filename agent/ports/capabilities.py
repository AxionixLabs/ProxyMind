# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
from agent.protocol import SubmitTurnCommand


class TurnExecutorResult(typing.Protocol):
    """约束主动 Turn 执行结果必须提供稳定状态。"""

    status: str


TurnResultValue = typing.TypeVar(
    "TurnResultValue",
    bound=TurnExecutorResult,
    covariant=True,
)


class TurnExecutor(typing.Protocol[TurnResultValue]):
    """执行一个已固定身份的主动 Turn，不持有 Session 状态。"""

    async def __call__(
        self,
        command: SubmitTurnCommand,
    ) -> TurnResultValue:
        """执行命令并返回具有稳定 status 的结果。"""
        ...


if __name__ == '__main__':
    pass
