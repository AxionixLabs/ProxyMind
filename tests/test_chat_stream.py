# -*- coding: utf-8 -*-

from unittest.mock import (
    AsyncMock,
    Mock,
)

import pytest

from mind_nova.requests import chat
from mind_nova.stream_events import (
    TextDeltaEvent,
    TurnDoneEvent,
    TurnFailedEvent,
    TurnLogicalSettledEvent,
)


class _PayloadStream(object):
    def __init__(self, payloads) -> None:
        self._payloads = payloads
        self._iterator = payloads.__aiter__()
        self.closed = False

    def __aiter__(self):
        return self

    async def __anext__(self):
        return await anext(self._iterator)

    async def aclose(self) -> None:
        self.closed = True
        close = getattr(self._payloads, "aclose", None)
        if callable(close):
            await close()


def _install_stream(monkeypatch, payloads) -> _PayloadStream:
    payload_stream = _PayloadStream(payloads)
    monkeypatch.setattr(chat, "build_chat_payload", AsyncMock(return_value={}))
    monkeypatch.setattr(chat.Channel, "make_headers", Mock(return_value={}))
    monkeypatch.setattr(chat.service_endpoints, "endpoint", Mock(return_value="url"))
    monkeypatch.setattr(chat, "streaming", Mock(return_value=payload_stream))
    return payload_stream


@pytest.mark.anyio
async def test_stream_chat_parses_events_and_filters_ping(monkeypatch) -> None:
    async def streaming(*_args, **_kwargs):
        yield {"type": "ping"}
        yield {
            "type": "text.delta",
            "segment_id": "segment-1",
            "text": "answer",
        }

    build_payload = AsyncMock(return_value={"message": "hello"})
    make_headers = Mock(return_value={"authorization": "test"})
    endpoint = Mock(return_value="https://example.com/chat")
    monkeypatch.setattr(chat, "build_chat_payload", build_payload)
    monkeypatch.setattr(chat.Channel, "make_headers", make_headers)
    monkeypatch.setattr(chat.service_endpoints, "endpoint", endpoint)
    monkeypatch.setattr(chat, "streaming", streaming)

    events = [
        event async for event in chat.stream_chat(
            {},
            "hello",
            [],
            timeout=12.0,
        )
    ]

    assert len(events) == 1
    assert isinstance(events[0], TextDeltaEvent)
    assert events[0].segment_id == "segment-1"
    assert events[0].text == "answer"
    build_payload.assert_awaited_once()
    endpoint.assert_called_once_with("/mind-chat")
    make_headers.assert_called_once_with()


@pytest.mark.anyio
async def test_failed_uses_absolute_settlement_deadline(monkeypatch) -> None:
    async def payloads():
        yield {"type": "turn.failed", "turn_id": "turn_1", "error": "timeout"}
        while True:
            await asyncio.sleep(0.005)
            yield {"type": "ping"}

    import asyncio

    monkeypatch.setattr(chat, "TURN_SETTLEMENT_TIMEOUT_SEC", 0.03)
    payload_stream = _install_stream(monkeypatch, payloads())
    event_stream = chat.stream_chat({}, "hello", [])

    events = [event async for event in event_stream]

    assert len(events) == 1
    assert isinstance(events[0], TurnFailedEvent)
    assert event_stream.end_reason == "settlement_timeout"
    assert payload_stream.closed is True


@pytest.mark.anyio
async def test_done_without_settlement_is_bounded(monkeypatch) -> None:
    async def payloads():
        yield {"type": "turn.done", "turn_id": "turn_1"}
        await asyncio.Event().wait()

    import asyncio

    monkeypatch.setattr(chat, "TURN_SETTLEMENT_TIMEOUT_SEC", 0.02)
    payload_stream = _install_stream(monkeypatch, payloads())
    event_stream = chat.stream_chat({}, "hello", [])

    events = [event async for event in event_stream]

    assert len(events) == 1
    assert isinstance(events[0], TurnDoneEvent)
    assert event_stream.end_reason == "settlement_timeout"
    assert payload_stream.closed is True


@pytest.mark.anyio
async def test_settlement_closes_stream_without_waiting_for_eof(monkeypatch) -> None:
    async def payloads():
        yield {"type": "turn.done", "turn_id": "turn_1"}
        yield {
            "type": "turn.logical_settled",
            "turn_id": "turn_1",
            "next_input": None,
        }
        await asyncio.Event().wait()

    import asyncio

    payload_stream = _install_stream(monkeypatch, payloads())
    event_stream = chat.stream_chat({}, "hello", [])

    events = [event async for event in event_stream]

    assert [type(event) for event in events] == [
        TurnDoneEvent,
        TurnLogicalSettledEvent,
    ]
    assert event_stream.end_reason == "settled"
    assert payload_stream.closed is True


@pytest.mark.anyio
async def test_disconnect_after_outcome_preserves_terminal_event(monkeypatch) -> None:
    async def payloads():
        yield {"type": "turn.failed", "turn_id": "turn_1", "error": "upstream"}
        raise OSError("connection lost")

    payload_stream = _install_stream(monkeypatch, payloads())
    event_stream = chat.stream_chat({}, "hello", [])

    events = [event async for event in event_stream]

    assert len(events) == 1
    assert isinstance(events[0], TurnFailedEvent)
    assert event_stream.end_reason == "disconnected"
    assert payload_stream.closed is True
