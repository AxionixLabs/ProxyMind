# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
import asyncio
from collections.abc import (
    Callable,
    Mapping
)
from agent.domain import (
    RunState,
    RunStatus
)
from agent.ports import (
    TurnExecutor,
    TurnExecutorResult
)
from agent.protocol import (
    RunEvent,
    SubmitTurnCommand
)
from agent.protocol.events import RunEventKind

ResultValue = typing.TypeVar("ResultValue", bound=TurnExecutorResult)

EventSink: typing.TypeAlias = Callable[[RunEvent], None]

_RESULT_STATES: dict[str, tuple[RunStatus, RunEventKind]] = {
    "completed": (RunStatus.COMPLETED, "run_completed"),
    "failed": (RunStatus.FAILED, "run_failed"),
    "incomplete": (RunStatus.INCOMPLETE, "run_incomplete"),
    "interrupted": (RunStatus.INTERRUPTED, "run_interrupted"),
    "reconciliation_required": (
        RunStatus.RECONCILIATION_REQUIRED,
        "run_reconciliation_required",
    ),
}


class RunActor(typing.Generic[ResultValue]):
    """作为单个 Run 的唯一状态写者调用外部 Turn 能力。"""

    def __init__(
        self,
        command: SubmitTurnCommand,
        executor: TurnExecutor[ResultValue],
        event_sink: EventSink,
    ) -> None:
        """绑定不可变命令、执行端口和同步事件出口。"""
        self.command = command
        self.executor = executor
        self.event_sink = event_sink
        self.state = RunState(
            session_id=command.session_id,
            run_id=command.run_id,
        )

    async def run(self) -> ResultValue:
        """执行 queued 到稳定结果的完整状态序列。"""
        self._transition(RunStatus.QUEUED, "run_queued")
        self._transition(RunStatus.RUNNING, "run_started")

        try:
            result = await self.executor(self.command)
        except asyncio.CancelledError:
            self._transition(
                RunStatus.CANCELLED,
                "run_cancelled",
                payload={"status": "cancelled"},
            )
            raise
        except (KeyboardInterrupt, SystemExit):
            self._transition(
                RunStatus.INTERRUPTED,
                "run_interrupted",
                payload={"status": "interrupted"},
            )
            raise
        except Exception as error:
            self._transition(
                RunStatus.FAILED,
                "run_failed",
                payload={
                    "status": "failed",
                    "error": {
                        "type": type(error).__name__,
                        "message": str(error),
                    },
                },
            )
            raise

        outcome = _RESULT_STATES.get(str(result.status or "").strip())
        if outcome is None:
            error = ValueError(
                f"unsupported turn result status: {result.status!r}"
            )
            self._transition(
                RunStatus.FAILED,
                "run_failed",
                payload={
                    "status": "failed",
                    "error": {
                        "type": type(error).__name__,
                        "message": str(error),
                    },
                },
            )
            raise error

        state, event_kind = outcome
        self._transition(
            state,
            event_kind,
            payload={"status": state.value},
        )
        return result

    def _transition(
        self,
        state: RunStatus,
        event_kind: RunEventKind,
        *,
        payload: Mapping[str, typing.Any] | None = None,
    ) -> None:
        """提交状态后发布具有连续序号的事实。"""
        self.state.transition(state)
        self.event_sink(RunEvent.create(
            sequence=self.state.next_sequence(),
            session_id=self.state.session_id,
            run_id=self.state.run_id,
            kind=event_kind,
            payload=payload or {"status": state.value},
            causation_id=self.command.command_id,
        ))



if __name__ == '__main__':
    pass
