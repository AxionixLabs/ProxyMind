# -*- coding: utf-8 -*-

import pytest

from agent.protocol import CanonicalItem
from agent.adapters.protocol.activity_events import TurnActivityProjector
from agent.ports import (
    AssistantOutputBoundary,
    AssistantBuffered,
    AssistantResponseSuperseded,
    AssistantSegmentCompleted,
    AssistantSettled,
    AssistantTextDelta,
    ModelWaitRequested,
    OutputSurfaceContext,
    ResponseIdentity,
    RetryChanged,
)
from agent.ports.transcript import TranscriptSink
from agent.adapters.protocol.model_events import ModelStreamEventHandler
from protocol.schema.stream_events import (
    StreamEvent,
    TextDeltaEvent,
    TextDoneEvent,
    TurnRetryingEvent,
)


class _Transcript(TranscriptSink):
    def __init__(self) -> None:
        self.entries: list[dict[str, object]] = []

    def append(self, event, *, actor=None, payload=None) -> None:
        self.entries.append({
            "event": event,
            "actor": actor,
            "payload": dict(payload or {}),
        })


class _Content:
    def __init__(self) -> None:
        self.items: list[object] = []

    async def emit(self, item: object) -> None:
        self.items.append(item)


class _Activity:
    """记录模型事件处理器投递的 typed activity 事实。"""

    def __init__(self) -> None:
        self.items: list[object] = []

    async def open(self) -> None:
        return None

    async def emit(self, item: object) -> None:
        self.items.append(item)

    async def close(self) -> None:
        return None


class _Projection:
    def __init__(self) -> None:
        self.current_item: CanonicalItem | None = None
        self.canonical_items: tuple[CanonicalItem, ...] = ()
        self.canonical_item_history: tuple[CanonicalItem, ...] = ()

    def update(
        self,
        current_item: CanonicalItem | None,
        *history: CanonicalItem,
    ) -> None:
        self.current_item = current_item
        self.canonical_item_history = tuple(history)
        self.canonical_items = tuple(
            item for item in history if not item.superseded
        )


def _handler() -> tuple[
    ModelStreamEventHandler,
    _Projection,
    _Transcript,
    _Content,
    _Activity,
]:
    """构造具有可观察端口的模型输出处理器。"""
    transcript = _Transcript()
    content = _Content()
    activity = _Activity()
    context = OutputSurfaceContext(
        surface_id="surface_test",
        cid="cid_test",
        sid="sid_test",
        turn_id="turn_test",
        agent_id="root",
    )
    handler = ModelStreamEventHandler(
        transcript=transcript,
        content=content,
        activity=TurnActivityProjector(context, activity),
    )
    return (
        handler,
        _Projection(),
        transcript,
        content,
        activity,
    )


def _item(
    item_id: str,
    text: str,
    *,
    status: str = "in_progress",
    attempt: int = 1,
    last_event_seq: int = 1,
    superseded: bool = False,
    superseded_by_attempt: int | None = None,
) -> CanonicalItem:
    """构造 presenter 使用的不可变正文 Item。"""
    return CanonicalItem(
        cid="cid_test",
        sid="sid_test",
        turn_id="turn_test",
        item_id=item_id,
        item_kind="text",
        item_status=status,
        presentation_epoch=1,
        round_no=1,
        attempt=attempt,
        first_event_seq=1,
        last_event_seq=last_event_seq,
        last_event_type=(
            "text.done" if status == "completed" else "text.delta"
        ),
        payload={"text": text},
        superseded=superseded,
        superseded_by_attempt=superseded_by_attempt,
    )


def _identity(*, attempt: int = 1) -> ResponseIdentity:
    """构造测试使用的响应身份。"""
    return ResponseIdentity(
        turn_id="turn_test",
        presentation_epoch=1,
        round=1,
        attempt=attempt,
    )


@pytest.mark.anyio
async def test_model_handler_projects_text_and_commits_transcript() -> None:
    (
        handler,
        projection,
        transcript,
        content,
        activity,
    ) = _handler()
    partial = _item("item-1", "answer")
    projection.update(partial, partial)

    delta_handled = await handler.handle(TextDeltaEvent(
        type="text.delta",
        turn_id="turn_test",
        segment_id="item-1",
        text="answer",
    ), projection=projection)
    completed = _item(
        "item-1",
        "answer",
        status="completed",
        last_event_seq=2,
    )
    projection.update(completed, completed)
    done_handled = await handler.handle(TextDoneEvent(
        type="text.done",
        turn_id="turn_test",
        segment_id="item-1",
    ), projection=projection)
    handler.flush_pending()

    assert delta_handled is True
    assert done_handled is True
    assert content.items == [
        AssistantTextDelta("answer", _identity(), item_id="item-1"),
        AssistantSegmentCompleted(_identity(), item_id="item-1"),
    ]
    assert transcript.entries == [{
        "event": "message.created",
        "actor": "assistant",
        "payload": {
            "content": "answer",
            "item_id": "item-1",
            "presentation_epoch": 1,
            "round": 1,
            "attempt": 1,
        },
    }]
    assert activity.items == [
        AssistantBuffered(
            surface_id="surface_test",
            turn_id="turn_test",
            identity=_identity(),
            item_id="item-1",
        ),
        AssistantSettled(
            surface_id="surface_test",
            turn_id="turn_test",
            identity=_identity(),
            item_id="item-1",
        ),
    ]


@pytest.mark.anyio
async def test_model_handler_completes_each_item_revision_once() -> None:
    (
        handler,
        projection,
        _transcript,
        content,
        _activity,
    ) = _handler()
    completed = _item("item-1", "answer", status="completed")
    projection.update(completed, completed)
    event = TextDoneEvent(
        type="text.done",
        turn_id="turn_test",
        segment_id="item-1",
    )

    await handler.handle(event, projection=projection)
    await handler.handle(event, projection=projection)

    assert content.items == [
        AssistantSegmentCompleted(_identity(), item_id="item-1"),
    ]


@pytest.mark.anyio
async def test_model_handler_supersedes_partial_provider_attempt() -> None:
    (
        handler,
        projection,
        transcript,
        content,
        activity,
    ) = _handler()
    old = _item("attempt-1", "old partial")
    projection.update(old, old)
    await handler.handle(TextDeltaEvent(
        type="text.delta",
        turn_id="turn_test",
        segment_id="attempt-1",
        text="old partial",
    ), projection=projection)

    superseded = _item(
        "attempt-1",
        "old partial",
        superseded=True,
        superseded_by_attempt=2,
    )
    projection.update(None, superseded)
    await handler.handle(TurnRetryingEvent(
        type="turn.retrying",
        turn_id="turn_test",
        round=1,
        attempt=2,
        max_attempts=3,
        retry_in_ms=20,
        reason="stream_reset",
    ), projection=projection)

    new = _item("attempt-2", "new answer", attempt=2, last_event_seq=3)
    projection.update(new, superseded, new)
    await handler.handle(TextDeltaEvent(
        type="text.delta",
        turn_id="turn_test",
        segment_id="attempt-2",
        text="new answer",
    ), projection=projection)
    completed = _item(
        "attempt-2",
        "new answer",
        status="completed",
        attempt=2,
        last_event_seq=4,
    )
    projection.update(completed, superseded, completed)
    await handler.handle(TextDoneEvent(
        type="text.done",
        turn_id="turn_test",
        segment_id="attempt-2",
    ), projection=projection)
    handler.flush_pending()

    assert content.items == [
        AssistantTextDelta("old partial", _identity(), item_id="attempt-1"),
        AssistantResponseSuperseded(
            turn_id="turn_test",
            presentation_epoch=1,
            round=1,
            attempt=2,
        ),
        AssistantTextDelta(
            "new answer",
            _identity(attempt=2),
            item_id="attempt-2",
        ),
        AssistantSegmentCompleted(
            _identity(attempt=2),
            item_id="attempt-2",
        ),
    ]
    assert [entry["event"] for entry in transcript.entries] == [
        "message.created",
        "message.superseded",
        "message.created",
    ]
    assert [
        item
        for item in activity.items
        if isinstance(item, RetryChanged)
    ] == [
        RetryChanged(
            surface_id="surface_test",
            turn_id="turn_test",
            source="provider",
            state="started",
            presentation_epoch=1,
            round=1,
            attempt=2,
        ),
        RetryChanged(
            surface_id="surface_test",
            turn_id="turn_test",
            source="provider",
            state="completed",
            presentation_epoch=1,
            round=1,
            attempt=2,
        ),
    ]


@pytest.mark.anyio
async def test_model_handler_flushes_output_at_structured_boundary() -> None:
    (
        handler,
        projection,
        transcript,
        content,
        _activity,
    ) = _handler()
    partial = _item("item-1", "partial")
    projection.update(partial, partial)
    await handler.handle(TextDeltaEvent(
        type="text.delta",
        turn_id="turn_test",
        segment_id="item-1",
        text="partial",
    ), projection=projection)
    projection.update(None, partial)

    handled = await handler.handle(StreamEvent(
        type="tool.builtin.call",
        turn_id="turn_test",
    ), projection=projection)

    assert handled is False
    assert content.items == [
        AssistantTextDelta("partial", _identity(), item_id="item-1"),
        AssistantOutputBoundary(),
    ]
    assert transcript.entries == [{
        "event": "message.created",
        "actor": "assistant",
        "payload": {
            "content": "partial",
            "item_id": "item-1",
            "presentation_epoch": 1,
            "round": 1,
            "attempt": 1,
        },
    }]


if __name__ == '__main__':
    pass
