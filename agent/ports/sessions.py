# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
from dataclasses import dataclass

from agent.protocol import (
    RunEvent,
    SubmitTurnCommand,
)
from .capabilities import (
    TurnExecutor,
    TurnExecutorResult
)
from .persistence import (
    RunPersistence,
    RunSnapshot
)

__all__ = (
    "RunExecution",
    "SessionRuntime",
    "SessionRuntimeFactory",
)

ResultValue = typing.TypeVar("ResultValue", bound=TurnExecutorResult)


@dataclass(frozen=True, slots=True)
class RunExecution(typing.Generic[ResultValue]):
    """保存一次 Session 命令的结果和完整本地事件投影。"""

    command_id: str
    run_id: str
    value: ResultValue
    events: tuple[RunEvent, ...]


class SessionRuntime(typing.Protocol[ResultValue]):
    """定义 Turn application 使用的 Session 生命周期端口。"""

    @property
    def closed(self) -> bool:
        """返回 Session runtime 是否已经完成关闭。"""
        ...

    async def execute(
        self,
        command: SubmitTurnCommand,
        executor: TurnExecutor[ResultValue],
    ) -> RunExecution[ResultValue]:
        """提交命令并返回执行结果。"""
        ...

    async def cancel_session(self, session_id: str) -> None:
        """取消指定 Session 的活动执行。"""
        ...

    async def close_session(self, session_id: str) -> None:
        """关闭指定 Session 并等待已接收命令收束。"""
        ...

    async def recover_session(
        self,
        session_id: str,
    ) -> tuple[RunSnapshot, ...]:
        """读取指定 Session 的未终结执行快照。"""
        ...

    async def close(self, *, cancel_running: bool = False) -> None:
        """关闭所有 Session。"""
        ...


class SessionRuntimeFactory(typing.Protocol[ResultValue]):
    """定义由组合根注入 Session runtime 实现的工厂端口。"""

    def __call__(
        self,
        persistence: RunPersistence | None = None,
    ) -> SessionRuntime[ResultValue]:
        """创建绑定指定持久化端口的 Session runtime。"""
        ...


if __name__ == '__main__':
    pass
