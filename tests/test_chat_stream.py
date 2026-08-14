# -*- coding: utf-8 -*-

import asyncio
from unittest.mock import (
    AsyncMock,
    Mock,
)

import httpx
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


def _install_reconnect_stream(monkeypatch, transport) -> None:
    monkeypatch.setattr(chat, "build_chat_payload", AsyncMock(return_value={
        "turn_id": "turn_001",
        "metadata": {"cid": "cid_1", "sid": "sid_1"},
    }))
    monkeypatch.setattr(chat.Channel, "make_headers", Mock(return_value={
        "authorization": "test",
    }))
    monkeypatch.setattr(
        chat.service_endpoints,
        "endpoint",
        lambda path: f"https://example.com{path}",
    )
    monkeypatch.setattr(chat, "streaming", transport)


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


@pytest.mark.anyio
async def test_disconnect_attaches_after_last_sequence_and_deduplicates_replay(
    monkeypatch,
) -> None:
    calls = []

    async def streaming(url, headers, payload, timeout):
        calls.append((url, headers, payload, timeout))
        if url.endswith("/mind-chat"):
            yield {
                "type": "text.delta",
                "turn_id": "turn_001",
                "event_seq": 1,
                "segment_id": "segment_1",
                "text": "first",
            }
            yield {
                "type": "text.delta",
                "turn_id": "turn_001",
                "event_seq": 2,
                "segment_id": "segment_1",
                "text": "second",
            }
            raise OSError("connection lost")

        yield {
            "type": "text.delta",
            "turn_id": "turn_001",
            "event_seq": 2,
            "segment_id": "segment_1",
            "text": "duplicate",
        }
        yield {
            "type": "text.delta",
            "turn_id": "turn_001",
            "event_seq": 3,
            "segment_id": "segment_1",
            "text": "third",
        }
        yield {
            "type": "turn.done",
            "turn_id": "turn_001",
            "event_seq": 4,
            "status": "completed",
        }
        yield {
            "type": "turn.logical_settled",
            "turn_id": "turn_001",
            "event_seq": 5,
            "next_input": None,
        }

    _install_reconnect_stream(monkeypatch, streaming)

    event_stream = chat.stream_chat({}, "hello", [], timeout=12.0)
    events = [event async for event in event_stream]

    assert [event.event_seq for event in events] == [1, 2, 3, 4, 5]
    assert [event.text for event in events if isinstance(event, TextDeltaEvent)] == [
        "first",
        "second",
        "third",
    ]
    assert event_stream.last_event_seq == 5
    assert event_stream.end_reason == "settled"
    assert [call[0] for call in calls] == [
        "https://example.com/mind-chat",
        "https://example.com/mind-attach",
    ]
    assert calls[1][2] == {
        "cid": "cid_1",
        "sid": "sid_1",
        "turn_id": "turn_001",
        "after_seq": 2,
    }


@pytest.mark.anyio
@pytest.mark.parametrize("status_code", [404, 422])
async def test_attach_does_not_retry_nonrecoverable_client_error(
    monkeypatch,
    status_code,
) -> None:
    calls = []

    async def streaming(url, _headers, _payload, _timeout):
        calls.append(url)
        if url.endswith("/mind-chat"):
            yield {
                "type": "text.delta",
                "turn_id": "turn_001",
                "event_seq": 1,
                "segment_id": "segment_1",
                "text": "first",
            }
            raise OSError("connection lost")

        response = httpx.Response(
            status_code,
            request=httpx.Request("POST", url),
        )
        response.raise_for_status()

    _install_reconnect_stream(monkeypatch, streaming)

    event_stream = chat.stream_chat({}, "hello", [])
    iterator = event_stream.__aiter__()

    event = await anext(iterator)
    assert event.event_seq == 1

    with pytest.raises(httpx.HTTPStatusError):
        await anext(iterator)

    assert calls == [
        "https://example.com/mind-chat",
        "https://example.com/mind-attach",
    ]
    assert event_stream.end_reason == "disconnected"


@pytest.mark.anyio
async def test_duplicate_replay_does_not_reset_attach_retry_budget(
    monkeypatch,
) -> None:
    calls = []

    async def streaming(url, _headers, payload, _timeout):
        calls.append((url, payload))
        yield {
            "type": "text.delta",
            "turn_id": "turn_001",
            "event_seq": 1,
            "segment_id": "segment_1",
            "text": "first" if url.endswith("/mind-chat") else "duplicate",
        }
        if url.endswith("/mind-chat"):
            raise OSError("connection lost")

    monkeypatch.setattr(chat, "ATTACH_RETRY_DELAYS_SEC", (0.0, 0.0, 0.0))
    _install_reconnect_stream(monkeypatch, streaming)

    event_stream = chat.stream_chat({}, "hello", [])
    events = [event async for event in event_stream]

    assert [event.event_seq for event in events] == [1]
    assert len(calls) == 4
    assert all(payload["after_seq"] == 1 for _, payload in calls[1:])
    assert event_stream.end_reason == "disconnected"


@pytest.mark.anyio
async def test_logical_settlement_alone_closes_recovery_stream(monkeypatch) -> None:
    async def payloads():
        yield {
            "type": "turn.logical_settled",
            "turn_id": "turn_001",
            "event_seq": 7,
            "next_input": None,
        }
        await asyncio.Event().wait()

    payload_stream = _install_stream(monkeypatch, payloads())
    event_stream = chat.stream_chat({}, "hello", [])

    events = [event async for event in event_stream]

    assert len(events) == 1
    assert isinstance(events[0], TurnLogicalSettledEvent)
    assert event_stream.end_reason == "settled"
    assert payload_stream.closed is True
