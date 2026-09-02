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


class _Status:
    """记录旧输出状态端口的过渡期调用。"""

    def __init__(self, events: list[tuple[typing.Any, ...]]) -> None:
        self.events = events

    async def begin_reply_wait_status(
        self,
        text: str | None = "Thinking",
        *,
        delay_sec: float = 0.28,
        animate_after_sec: float | None = None,
    ) -> None:
        self.events.append(("status.wait", delay_sec, animate_after_sec))


class _Handler:
    """记录批次完整后释放的工具调用。"""

    def __init__(self, events: list[tuple[typing.Any, ...]]) -> None:
        self.events = events

    async def handle_call(self, event: ToolCallEvent) -> ToolCallHandlingResult:
        self.events.append(("tool.handle", event.call_id))
        return ToolCallHandlingResult.handled()


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
        status_control=_Status(events),
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

    assert result == ToolDispatchResult("handled")
    assert events == [
        ("batch.started", "batch_1"),
        ("status.wait", 0.15, 0.85),
        ("tool.started", "call_1", "client", "read_file"),
        ("tool.started", "call_2", "plan", "plan_steps"),
        ("batch.completed", "batch_1"),
        ("status.wait", 0.75, None),
        ("tool.handle", "call_1"),
        ("tool.handle", "call_2"),
    ]


if __name__ == '__main__':
    pass
