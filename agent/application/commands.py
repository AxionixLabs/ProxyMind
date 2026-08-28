# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
import asyncio
from dataclasses import dataclass
from agent.ports import (
    TurnExecutor,
    TurnExecutorResult
)
from agent.protocol import (
    RunEvent,
    SubmitTurnCommand
)
from agent.runtime import SessionLoop

ResultValue = typing.TypeVar("ResultValue", bound=TurnExecutorResult)


@dataclass(frozen=True, slots=True)
class SubmitTurnResult(typing.Generic[ResultValue]):
    """返回主动 Turn 的原始结果和本地事件投影。"""

    value: ResultValue
    events: tuple[RunEvent, ...]


async def submit_turn(
    command: SubmitTurnCommand,
    executor: TurnExecutor[ResultValue],
) -> SubmitTurnResult[ResultValue]:
    """在短生命周期 SessionLoop 中执行一个主动 Turn 命令。"""
    session = SessionLoop(command.session_id, executor)
    try:
        execution = await session.execute(command)
    except asyncio.CancelledError:
        await session.close(cancel_running=True)
        raise
    except BaseException:
        await session.close()
        raise
    else:
        await session.close()
        return SubmitTurnResult(
            value=execution.value,
            events=execution.events,
        )


if __name__ == '__main__':
    pass
