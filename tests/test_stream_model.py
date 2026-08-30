# -*- coding: utf-8 -*-

from unittest.mock import Mock

import pytest

from mind_app.output import (
    AssistantOutputBoundary,
    AssistantResponseSuperseded,
    AssistantSegmentCompleted,
    AssistantTextDelta,
    OutputStatusPort,
    ResponseIdentity,
)
from mind_app.history.contracts import TranscriptSink
from mind_app.runtime.turns.stream_model import ModelStreamEventHandler
from mind_nova.stream_events import (
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


class _Status(OutputStatusPort):
    def __init__(self) -> None:
        self.reply_wait_calls = 0

    async def begin_tool_status(self) -> None:
        return None

    async def begin_custom_tool_status(self, text: str | None) -> None:
        _ = text
        return None

    async def begin_reply_wait_status(
        self,
        text: str | None = "Thinking",
        *,
        delay_sec: float = 0.28,
        animate_after_sec: float | None = None,
    ) -> None:
        _ = (text, delay_sec, animate_after_sec)
        self.reply_wait_calls += 1

    async def end_status(self, *, immediate: bool = False) -> None:
        _ = immediate
        return None


def _handler() -> tuple[
    ModelStreamEventHandler,
    _Transcript,
    _Content,
    _Status,
    Mock,
    Mock,
]:
    """构造具有可观察端口的模型输出处理器。"""
    transcript = _Transcript()
    content = _Content()
    status = _Status()
    retry = Mock()
    idle = Mock()
    handler = ModelStreamEventHandler(
        transcript=transcript,
        content=content,
        status_control=status,
        provider_retry_sink=retry,
        idle_reschedule=idle,
    )
    return handler, transcript, content, status, retry, idle


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
    handler, transcript, content, status, retry, idle = _handler()

    delta_handled = await handler.handle(TextDeltaEvent(
        type="text.delta",
        turn_id="turn_test",
        segment_id="item-1",
        text="answer",
    ))
    done_handled = await handler.handle(TextDoneEvent(
        type="text.done",
        turn_id="turn_test",
        segment_id="item-1",
    ))
    handler.flush_pending()

    assert delta_handled is True
    assert done_handled is True
    assert handler.sources == ()
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
    assert status.reply_wait_calls == 1
    assert retry.call_args_list == [((False,), {}), ((False,), {})]
    idle.assert_called_once_with()


@pytest.mark.anyio
async def test_model_handler_supersedes_partial_provider_attempt() -> None:
    handler, transcript, content, status, retry, _idle = _handler()

    await handler.handle(TextDeltaEvent(
        type="text.delta",
        turn_id="turn_test",
        segment_id="attempt-1",
        text="old partial",
    ))
    await handler.handle(TurnRetryingEvent(
        type="turn.retrying",
        turn_id="turn_test",
        round=1,
        attempt=2,
        max_attempts=3,
        retry_in_ms=20,
        reason="stream_reset",
    ))
    await handler.handle(TextDeltaEvent(
        type="text.delta",
        turn_id="turn_test",
        segment_id="attempt-2",
        text="new answer",
    ))
    await handler.handle(TextDoneEvent(
        type="text.done",
        turn_id="turn_test",
        segment_id="attempt-2",
    ))
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
    assert status.reply_wait_calls == 2
    assert retry.call_args_list == [
        ((False,), {}),
        ((True,), {}),
        ((False,), {}),
        ((False,), {}),
    ]


@pytest.mark.anyio
async def test_model_handler_flushes_output_at_structured_boundary() -> None:
    handler, transcript, content, _status, _retry, _idle = _handler()
    await handler.handle(TextDeltaEvent(
        type="text.delta",
        turn_id="turn_test",
        segment_id="item-1",
        text="partial",
    ))

    handled = await handler.handle(StreamEvent(
        type="tool.builtin.call",
        turn_id="turn_test",
    ))

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
