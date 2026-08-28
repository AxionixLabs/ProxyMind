# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
from agent.protocol import SubmitTurnCommand


class TurnExecutorResult(typing.Protocol):
    """约束主动 Turn 执行结果必须提供稳定状态和协议字典。"""

    status: str

    def to_dict(self) -> dict[str, typing.Any]:
        """返回不包含运行时对象的结构化结果。"""
        ...


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
