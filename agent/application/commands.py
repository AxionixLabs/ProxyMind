# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
import asyncio
from dataclasses import dataclass
from agent.ports import (
    RunFact,
    RunPersistence,
    RunSnapshot,
    TurnExecutor,
    TurnExecutorResult
)
from agent.protocol import (
    RunEvent,
    SubmitTurnCommand
)
from agent.runtime.session_runtime import SessionRuntimeOwner
from .projections import (
    RunResultProjection,
    project_run_result
)

ResultValue = typing.TypeVar("ResultValue", bound=TurnExecutorResult)


@dataclass(frozen=True, slots=True)
class SubmitTurnResult(typing.Generic[ResultValue]):
    """返回主动 Turn 的原始结果和本地事件投影。"""

    value: ResultValue
    events: tuple[RunEvent, ...]
    projection: RunResultProjection


class TurnApplication(typing.Generic[ResultValue]):
    """编排主动 Turn 提交、事件投影和 Session 生命周期操作。"""

    def __init__(self, persistence: RunPersistence | None = None) -> None:
        """创建由 runtime owner 持有 Session 可变状态的应用入口。"""
        self._persistence = persistence
        self._runtime: SessionRuntimeOwner[ResultValue] = (
            SessionRuntimeOwner(persistence)
        )

    @property
    def closed(self) -> bool:
        """返回底层 Session runtime 是否已经完成关闭。"""
        return self._runtime.closed

    async def submit(
        self,
        command: SubmitTurnCommand,
        executor: TurnExecutor[ResultValue],
    ) -> SubmitTurnResult[ResultValue]:
        """提交命令并从该 Run 的完整事件序列生成稳定结果投影。"""
        try:
            execution = await self._runtime.execute(command, executor)
        except asyncio.CancelledError:
            await self._runtime.cancel_session(command.session_id)
            raise
        return SubmitTurnResult(
            value=execution.value,
            events=execution.events,
            projection=project_run_result(execution.events),
        )

    async def cancel_session(self, session_id: str) -> None:
        """取消指定 Session 的活动 Run 和排队命令，并等待清理完成。"""
        await self._runtime.cancel_session(session_id)

    async def close_session(self, session_id: str) -> None:
        """关闭指定 Session，并等待已接收命令自然收束。"""
        await self._runtime.close_session(session_id)

    async def recover_session(
        self,
        session_id: str,
    ) -> tuple[RunSnapshot, ...]:
        """读取未终结 Run 及其安全恢复动作，不自动调用执行端口。"""
        return await self._runtime.recover_session(session_id)

    async def events(
        self,
        run_id: str,
        *,
        after_sequence: int = 0,
    ) -> tuple[RunEvent, ...]:
        """按本地事件游标读取已经持久提交的 Run 事件。"""
        if self._persistence is None:
            return ()
        return await self._persistence.load_events(
            run_id,
            after_sequence=after_sequence,
        )

    async def facts(
        self,
        run_id: str,
        *,
        kind: str | None = None,
    ) -> tuple[RunFact, ...]:
        """读取最终消息、工具结果、审批决定和证据引用。"""
        if self._persistence is None:
            return ()
        return await self._persistence.load_facts(run_id, kind=kind)

    async def close(self, *, cancel_running: bool = False) -> None:
        """关闭 application 管理的全部 Session。"""
        await self._runtime.close(cancel_running=cancel_running)


async def submit_turn(
    command: SubmitTurnCommand,
    executor: TurnExecutor[ResultValue],
) -> SubmitTurnResult[ResultValue]:
    """在短生命周期 SessionLoop 中执行一个主动 Turn 命令。"""
    application: TurnApplication[ResultValue] = TurnApplication()
    try:
        return await application.submit(command, executor)
    finally:
        await application.close()


if __name__ == '__main__':
    pass
