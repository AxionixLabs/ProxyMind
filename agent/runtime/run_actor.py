# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
import asyncio
from collections.abc import (
    Awaitable,
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

EventSink: typing.TypeAlias = Callable[[RunEvent], Awaitable[None]]

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
        *,
        state: RunState | None = None,
    ) -> None:
        """绑定不可变命令、执行端口、持久事件出口和可选恢复状态。"""
        self.command = command
        self.executor = executor
        self.event_sink = event_sink
        self.state = state or RunState(
            session_id=command.session_id,
            run_id=command.run_id,
        )

    async def enqueue(self) -> None:
        """在进入 Session 队列前持久提交 queued 状态。"""
        if self.state.status is not RunStatus.CREATED:
            raise RuntimeError("only a created run can be enqueued")
        await self._transition(RunStatus.QUEUED, "run_queued")

    async def run(self) -> ResultValue:
        """执行 queued 到稳定结果的完整状态序列。"""
        if self.state.status is RunStatus.CREATED:
            await self.enqueue()
        if self.state.status is not RunStatus.QUEUED:
            raise RuntimeError("run actor must start execution from queued")
        await self._transition(RunStatus.RUNNING, "run_started")

        try:
            result = await self.executor(self.command)
            state, event_kind, payload = self._terminal_result(result)
        except asyncio.CancelledError:
            await self._transition(
                RunStatus.CANCELLED,
                "run_cancelled",
                payload={"status": "cancelled"},
            )
            raise
        except (KeyboardInterrupt, SystemExit):
            await self._transition(
                RunStatus.INTERRUPTED,
                "run_interrupted",
                payload={"status": "interrupted"},
            )
            raise
        except TimeoutError as error:
            await self._transition(
                RunStatus.RECONCILIATION_REQUIRED,
                "run_reconciliation_required",
                payload={
                    "status": "reconciliation_required",
                    "error": {
                        "type": type(error).__name__,
                        "message": str(error),
                    },
                },
            )
            raise
        except Exception as error:
            await self._transition(
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

        await self._transition(state, event_kind, payload=payload)
        return result

    async def cancel_queued(self) -> None:
        """把尚未开始派发的排队 Run 收束为显式取消。"""
        if self.state.status is RunStatus.QUEUED:
            await self._transition(
                RunStatus.CANCELLED,
                "run_cancelled",
                payload={"status": "cancelled"},
            )

    @staticmethod
    def _terminal_result(
        result: ResultValue,
    ) -> tuple[RunStatus, RunEventKind, dict[str, typing.Any]]:
        """校验执行结果并构建终态事件载荷。"""
        status = str(result.status or "").strip()
        outcome = _RESULT_STATES.get(status)
        if outcome is None:
            raise ValueError(f"unsupported turn result status: {result.status!r}")

        result_payload = result.to_dict()
        if not isinstance(result_payload, dict):
            raise TypeError("turn result payload must be an object")
        if str(result_payload.get("status") or "").strip() != status:
            raise ValueError(
                "turn result payload status does not match result status"
            )

        state, event_kind = outcome
        return state, event_kind, {
            "status": state.value,
            "result": result_payload,
        }

    async def _transition(
        self,
        state: RunStatus,
        event_kind: RunEventKind,
        *,
        payload: Mapping[str, typing.Any] | None = None,
    ) -> None:
        """构造连续事实，由事件出口持久提交成功后再更新内存状态。"""
        previous_status = self.state.status
        previous_sequence = self.state.sequence
        self.state.transition(state)
        try:
            event = RunEvent.create(
                sequence=self.state.next_sequence(),
                session_id=self.state.session_id,
                run_id=self.state.run_id,
                kind=event_kind,
                payload=payload or {"status": state.value},
                causation_id=self.command.command_id,
            )
            await self.event_sink(event)
        except BaseException:
            self.state.status = previous_status
            self.state.sequence = previous_sequence
            raise



if __name__ == '__main__':
    pass
