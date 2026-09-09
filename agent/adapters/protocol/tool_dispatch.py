# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import asyncio
import typing
from dataclasses import dataclass

from agent.adapters.protocol.activity_events import TurnActivityProjector
from agent.adapters.protocol.tool_events import (
    ToolCallBatchBuffer,
    ToolEventHandler,
    tool_activity_kind,
)
from agent.ports import TransportRecoveryPhase
from protocol.schema.stream_events import (
    StreamEvent,
    ToolBuiltinCallEvent,
    ToolBuiltinDoneEvent,
    ToolCallEvent,
    ToolCallsDoneEvent,
    ToolCallsStartEvent,
    ToolOutputEvent,
)


ToolDispatchStatus = typing.Literal[
    "unhandled",
    "handled",
    "interrupted",
]


@dataclass(frozen=True, slots=True)
class ToolDispatchResult:
    """描述一条流事件是否由工具分派器消费。"""

    status: ToolDispatchStatus
    error: str | None = None

    def __post_init__(self) -> None:
        """校验分派结果的有限状态。"""
        if self.status not in {"unhandled", "handled", "interrupted"}:
            raise ValueError("tool dispatch status is invalid")


class StreamToolDispatcher:
    """拥有单个 Turn 的工具批次组装和活动事件分派。"""

    def __init__(
        self,
        *,
        handler: ToolEventHandler,
        activity: TurnActivityProjector,
        replay_target_seq: int | None = None,
    ) -> None:
        """绑定工具处理器、活动投影器和输出状态端口。"""
        if replay_target_seq is not None and (
            not isinstance(replay_target_seq, int)
            or isinstance(replay_target_seq, bool)
            or replay_target_seq < 0
        ):
            raise ValueError("tool replay target must be non-negative")
        self.handler = handler
        self.activity = activity
        self.batch: ToolCallBatchBuffer = ToolCallBatchBuffer()
        self._replay_target_seq = replay_target_seq
        self._replayed_calls: list[ToolCallEvent] = []
        self._recovery_calls: list[ToolCallEvent] = []
        self._calls: asyncio.Queue[tuple[ToolCallEvent, ...]] = asyncio.Queue()
        self._completions: asyncio.Queue[ToolDispatchResult | Exception] = asyncio.Queue()
        self._worker: asyncio.Task[None] | None = None
        self._closed = False

    @property
    def batch_active(self) -> bool:
        """返回当前是否存在未完整接收的工具批次。"""
        return self.batch.active

    def _is_historical(self, event: StreamEvent) -> bool:
        """返回事件是否属于 attach 建立时冻结的历史前缀。"""
        target = self._replay_target_seq
        event_seq = event.event_seq
        return (
            target is not None
            and event_seq is not None
            and event_seq <= target
        )

    async def dispatch(self, event: StreamEvent) -> ToolDispatchResult:
        """按事件类型维护工具 lease 并执行已完整的客户端调用。"""
        if isinstance(event, ToolBuiltinCallEvent):
            await self.activity.tool_started(
                event.builtin_call_id,
                "builtin",
                name=event.builtin_type,
            )
            return ToolDispatchResult("handled")

        if isinstance(event, ToolBuiltinDoneEvent):
            await self.activity.tool_completed_and_wait(
                event.builtin_call_id,
                "builtin",
                name=event.builtin_type,
            )
            return ToolDispatchResult("handled")

        if isinstance(event, ToolCallsStartEvent):
            self.batch.begin(event)
            await self.activity.tool_batch_started(event.batch_id)
            return ToolDispatchResult("handled")

        if isinstance(event, ToolCallsDoneEvent):
            ready_calls = self.batch.complete(event)
            for ready_call in ready_calls:
                await self.activity.tool_started(
                    ready_call.call_id,
                    tool_activity_kind(ready_call.name),
                    name=ready_call.name,
                )
            await self.activity.tool_batch_completed(event.batch_id)
            if self._is_historical(event):
                self._replayed_calls.extend(ready_calls)
                return ToolDispatchResult("handled")
            return self._enqueue_calls(ready_calls)

        if isinstance(event, ToolCallEvent):
            return self._enqueue_calls(self.batch.accept(event))

        if isinstance(event, ToolOutputEvent):
            await self.handler.handle_output(event)
            return ToolDispatchResult("handled")

        return ToolDispatchResult("unhandled")

    async def prepare_replay_completion(self) -> None:
        """追平前对历史调用执行权威状态裁决，不触发未决副作用。"""
        replayed_calls = tuple(self._replayed_calls)
        recovery_calls: list[ToolCallEvent] = []
        for event in replayed_calls:
            action = await self.handler.classify_replayed_call(event)
            if action == "skip":
                await self.handler.complete_replayed_call(event)
            else:
                recovery_calls.append(event)
        self._replayed_calls.clear()
        self._recovery_calls.extend(recovery_calls)
        self._replay_target_seq = None

    async def execute_recovery_calls(self) -> ToolDispatchResult:
        """追平权威水位后接管仍等待结果的工具调用。"""
        recovery_calls = tuple(self._recovery_calls)
        self._recovery_calls.clear()
        return self._enqueue_calls(recovery_calls)

    async def transport_recovery_changed(
        self,
        phase: TransportRecoveryPhase,
        event_seq: int,
    ) -> None:
        """按传输恢复阶段裁决历史调用并恢复实时活动投影。"""
        if phase == "caught_up":
            await self.prepare_replay_completion()
        await self.activity.transport_recovery_changed(phase, event_seq)
        if phase != "caught_up":
            return
        await self.execute_recovery_calls()

    def _enqueue_calls(self, calls: tuple[ToolCallEvent, ...]) -> ToolDispatchResult:
        """将已验证调用交给本 Turn 唯一的顺序执行任务。"""
        if not calls:
            return ToolDispatchResult("handled")
        if self._closed:
            raise RuntimeError("tool dispatcher is closed")
        self._calls.put_nowait(calls)
        if self._worker is None:
            self._worker = asyncio.create_task(self._run_calls())
        return ToolDispatchResult("handled")

    async def _run_calls(self) -> None:
        """顺序执行工具并把完成或失败交回事件消费所有者。"""
        while True:
            calls = await self._calls.get()
            try:
                result = await self._execute_calls(calls)
            except asyncio.CancelledError:
                if not self._closed:
                    self._completions.put_nowait(ToolDispatchResult("interrupted"))
                raise
            except Exception as error:
                self._completions.put_nowait(error)
                return
            else:
                self._completions.put_nowait(result)
                if result.status == "interrupted":
                    return
            finally:
                self._calls.task_done()

    async def next_completion(self) -> ToolDispatchResult:
        """向事件消费所有者交付一次工具批次的结果或异常。"""
        result = await self._completions.get()
        if isinstance(result, Exception):
            raise result
        return result

    async def aclose(self) -> None:
        """取消并回收当前工具等待，底层后台进程仍由进程管理器持有。"""
        self._closed = True
        worker = self._worker
        self._worker = None
        if worker is not None:
            worker.cancel()
            try:
                await worker
            except asyncio.CancelledError:
                pass

    async def _execute_calls(
        self,
        calls: tuple[ToolCallEvent, ...],
    ) -> ToolDispatchResult:
        """顺序执行已完成批次验证的客户端工具调用。"""
        for event in calls:
            handling = await self.handler.handle_call(event)
            if handling.status == "interrupted":
                return ToolDispatchResult("interrupted", handling.error)
        return ToolDispatchResult("handled")


if __name__ == '__main__':
    pass
