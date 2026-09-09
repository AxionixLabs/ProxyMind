# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing

import pytest

from agent.adapters.protocol.tool_dispatch import (
    StreamToolDispatcher,
    ToolDispatchResult,
)
from agent.adapters.protocol.tool_events import ToolCallHandlingResult
from protocol.schema.stream_events import (
    ToolCallEvent,
    ToolCallsDoneEvent,
    ToolCallsStartEvent,
)


class _Activity:
    """记录工具分派器产生的展示事实。"""

    def __init__(self, events: list[tuple[typing.Any, ...]]) -> None:
        self.events = events

    async def tool_batch_started(self, batch_id: str) -> None:
        self.events.append(("batch.started", batch_id))

    async def tool_batch_completed(self, batch_id: str) -> None:
        self.events.append(("batch.completed", batch_id))

    async def tool_started(
        self,
        tool_id: str,
        tool_kind: str,
        *,
        name: str = "",
    ) -> None:
        self.events.append(("tool.started", tool_id, tool_kind, name))

    async def transport_recovery_changed(
        self,
        phase: str,
        event_seq: int,
    ) -> None:
        self.events.append(("recovery", phase, event_seq))


class _Handler:
    """记录批次完整后释放的工具调用。"""

    def __init__(self, events: list[tuple[typing.Any, ...]]) -> None:
        self.events = events

    async def handle_call(self, event: ToolCallEvent) -> ToolCallHandlingResult:
        self.events.append(("tool.handle", event.call_id))
        return ToolCallHandlingResult.handled()

    async def classify_replayed_call(self, event: ToolCallEvent) -> str:
        self.events.append(("tool.classify", event.call_id))
        return "skip"

    async def complete_replayed_call(self, event: ToolCallEvent) -> None:
        self.events.append(("tool.replayed", event.call_id))


def _scope() -> dict[str, typing.Any]:
    """返回工具批次事件的固定 Turn scope。"""
    return {
        "proto": "mind.chat",
        "cid": "cid_test",
        "sid": "sid_test",
        "turn_id": "turn_test",
        "presentation_epoch": 1,
    }


@pytest.mark.anyio
async def test_tool_batch_starts_every_lease_before_execution() -> None:
    """验证批次完整前不执行工具且完整后先取得全部 lease。"""
    events: list[tuple[typing.Any, ...]] = []
    dispatcher = StreamToolDispatcher(
        handler=_Handler(events),
        activity=_Activity(events),
    )
    start = ToolCallsStartEvent(
        type="tool.calls.start",
        **_scope(),
        batch_id="batch_1",
        call_ids=("call_1", "call_2"),
        count=2,
    )
    first = ToolCallEvent(
        type="tool.call",
        **_scope(),
        call_id="call_1",
        name="read_file",
    )
    second = ToolCallEvent(
        type="tool.call",
        **_scope(),
        call_id="call_2",
        name="plan_steps",
    )
    done = ToolCallsDoneEvent(
        type="tool.calls.done",
        **_scope(),
        batch_id="batch_1",
        call_ids=("call_1", "call_2"),
        count=2,
    )

    await dispatcher.dispatch(start)
    await dispatcher.dispatch(first)
    await dispatcher.dispatch(second)
    assert not any(event[0] == "tool.handle" for event in events)

    result = await dispatcher.dispatch(done)
    assert await dispatcher.next_completion() == ToolDispatchResult("handled")
    await dispatcher.aclose()

    assert result == ToolDispatchResult("handled")
    assert events == [
        ("batch.started", "batch_1"),
        ("tool.started", "call_1", "client", "read_file"),
        ("tool.started", "call_2", "plan", "plan_steps"),
        ("batch.completed", "batch_1"),
        ("tool.handle", "call_1"),
        ("tool.handle", "call_2"),
    ]


@pytest.mark.anyio
async def test_historical_tool_batch_is_classified_without_reexecution() -> None:
    """验证恢复水位内的工具只归约，追平后的新调用才执行。"""
    events: list[tuple[typing.Any, ...]] = []
    dispatcher = StreamToolDispatcher(
        handler=_Handler(events),
        activity=_Activity(events),
        replay_target_seq=3,
    )

    async def dispatch_batch(
        batch_id: str,
        call_id: str,
        *,
        first_event_seq: int,
    ) -> None:
        await dispatcher.dispatch(ToolCallsStartEvent(
            type="tool.calls.start",
            **_scope(),
            event_seq=first_event_seq,
            batch_id=batch_id,
            call_ids=(call_id,),
            count=1,
        ))
        await dispatcher.dispatch(ToolCallEvent(
            type="tool.call",
            **_scope(),
            event_seq=first_event_seq + 1,
            call_id=call_id,
            name="exec_command",
        ))
        await dispatcher.dispatch(ToolCallsDoneEvent(
            type="tool.calls.done",
            **_scope(),
            event_seq=first_event_seq + 2,
            batch_id=batch_id,
            call_ids=(call_id,),
            count=1,
        ))

    await dispatch_batch("batch_replayed", "call_replayed", first_event_seq=1)
    assert ("tool.handle", "call_replayed") not in events

    await dispatcher.transport_recovery_changed("caught_up", 3)
    assert ("tool.classify", "call_replayed") in events
    assert ("tool.replayed", "call_replayed") in events
    assert ("tool.handle", "call_replayed") not in events

    await dispatch_batch("batch_live", "call_live", first_event_seq=4)
    await dispatcher.next_completion()
    await dispatcher.aclose()
    assert ("tool.handle", "call_live") in events


if __name__ == '__main__':
    pass
