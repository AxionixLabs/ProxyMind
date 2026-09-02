# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
from dataclasses import dataclass

from agent.adapters.protocol.activity_events import TurnActivityProjector
from agent.adapters.protocol.tool_events import (
    ToolCallBatchBuffer,
    ToolEventHandler,
    tool_activity_kind,
)
from agent.ports import OutputStatusPort
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
        status_control: OutputStatusPort,
    ) -> None:
        """绑定工具处理器、活动投影器和输出状态端口。"""
        self.handler = handler
        self.activity = activity
        self.status_control = status_control
        self.batch = ToolCallBatchBuffer()

    @property
    def batch_active(self) -> bool:
        """返回当前是否存在未完整接收的工具批次。"""
        return self.batch.active

    async def dispatch(self, event: StreamEvent) -> ToolDispatchResult:
        """按事件类型维护工具 lease 并执行已完整的客户端调用。"""
        if isinstance(event, ToolBuiltinCallEvent):
            await self.activity.tool_started(
                event.builtin_call_id,
                "builtin",
                name=event.builtin_type,
            )
            await self.status_control.begin_tool_status()
            return ToolDispatchResult("handled")

        if isinstance(event, ToolBuiltinDoneEvent):
            await self.activity.tool_completed(
                event.builtin_call_id,
                "builtin",
                name=event.builtin_type,
            )
            await self.activity.request_model_wait("tool_result")
            await self.status_control.end_status()
            return ToolDispatchResult("handled")

        if isinstance(event, ToolCallsStartEvent):
            self.batch.begin(event)
            await self.activity.tool_batch_started(event.batch_id)
            await self.status_control.begin_reply_wait_status(
                delay_sec=0.15,
                animate_after_sec=0.85,
            )
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
            await self.status_control.begin_reply_wait_status(delay_sec=0.75)
            return await self._execute_calls(ready_calls)

        if isinstance(event, ToolCallEvent):
            return await self._execute_calls(self.batch.accept(event))

        if isinstance(event, ToolOutputEvent):
            await self.handler.handle_output(event)
            return ToolDispatchResult("handled")

        return ToolDispatchResult("unhandled")

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
