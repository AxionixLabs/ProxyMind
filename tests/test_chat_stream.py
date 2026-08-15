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


async def _collect(stream):
    return [event async for event in stream]


@pytest.mark.anyio
async def test_stream_chat_parses_events_and_filters_ping(monkeypatch) -> None:
    async def streaming(*_args, **_kwargs):
        yield {"type": "ping"}
        yield {
            "type": "text.delta",
            "turn_id": "turn_001",
            "segment_id": "segment-1",
            "text": "answer",
        }
        yield {
            "type": "turn.logical_settled",
            "turn_id": "turn_001",
            "next_input": None,
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

    assert len(events) == 2
    assert isinstance(events[0], TextDeltaEvent)
    assert events[0].segment_id == "segment-1"
    assert events[0].text == "answer"
    assert isinstance(events[1], TurnLogicalSettledEvent)
    assert events[1].turn_id == "turn_001"
    build_payload.assert_awaited_once()
    endpoint.assert_called_once_with("/mind-chat")
    make_headers.assert_called_once_with()


@pytest.mark.anyio
async def test_failed_waits_for_logical_settlement_without_timeout(monkeypatch) -> None:
    release = asyncio.Event()

    async def payloads():
        yield {"type": "turn.failed", "turn_id": "turn_1", "error": "timeout"}
        await release.wait()
        yield {
            "type": "turn.logical_settled",
            "turn_id": "turn_1",
            "next_input": None,
        }

    payload_stream = _install_stream(monkeypatch, payloads())
    event_stream = chat.stream_chat({}, "hello", [])
    consuming = asyncio.create_task(_collect(event_stream))

    await asyncio.sleep(0.03)
    assert not consuming.done()

    release.set()
    events = await asyncio.wait_for(consuming, timeout=1.0)

    assert len(events) == 2
    assert isinstance(events[0], TurnFailedEvent)
    assert isinstance(events[1], TurnLogicalSettledEvent)
    assert event_stream.end_reason == "settled"
    assert payload_stream.closed is True


@pytest.mark.anyio
async def test_done_without_settlement_stays_observed_until_cancelled(monkeypatch) -> None:
    async def payloads():
        yield {"type": "turn.done", "turn_id": "turn_1"}
        await asyncio.Event().wait()

    payload_stream = _install_stream(monkeypatch, payloads())
    event_stream = chat.stream_chat({}, "hello", [])
    consuming = asyncio.create_task(_collect(event_stream))

    await asyncio.sleep(0.03)
    assert not consuming.done()

    consuming.cancel()
    with pytest.raises(asyncio.CancelledError):
        await consuming

    assert event_stream.end_reason == "cancelled"
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
async def test_disconnect_after_outcome_recovers_until_settlement(monkeypatch) -> None:
    calls = []
    reconnecting = []

    async def streaming(url, _headers, _payload, _timeout):
        calls.append(url)
        if url.endswith("/mind-chat"):
            yield {
                "type": "turn.failed",
                "turn_id": "turn_001",
                "event_seq": 1,
                "error": "upstream",
            }
            raise OSError("connection lost")
        yield {
            "type": "turn.logical_settled",
            "turn_id": "turn_001",
            "event_seq": 2,
            "next_input": None,
        }

    _install_reconnect_stream(monkeypatch, streaming)
    event_stream = chat.stream_chat(
        {},
        "hello",
        [],
        on_reconnect_status=reconnecting.append,
    )

    events = [event async for event in event_stream]

    assert len(events) == 2
    assert isinstance(events[0], TurnFailedEvent)
    assert isinstance(events[1], TurnLogicalSettledEvent)
    assert event_stream.end_reason == "settled"
    assert len(calls) == 2
    assert reconnecting == [True, False]


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
async def test_chat_conflict_does_not_attach_reused_turn(monkeypatch) -> None:
    calls = []

    async def streaming(url, _headers, _payload, _timeout):
        calls.append(url)
        if url.endswith("/mind-chat"):
            response = httpx.Response(
                409,
                json={"detail": {"code": "turn_id_reused"}},
                request=httpx.Request("POST", url),
            )
            response.raise_for_status()
        yield {
            "type": "turn.logical_settled",
            "turn_id": "turn_001",
            "event_seq": 1,
            "next_input": None,
        }

    _install_reconnect_stream(monkeypatch, streaming)
    event_stream = chat.stream_chat({}, "new request", [])

    with pytest.raises(httpx.HTTPStatusError):
        async for _event in event_stream:
            pass

    assert calls == ["https://example.com/mind-chat"]
    assert event_stream.end_reason == "fatal"


@pytest.mark.anyio
async def test_disconnect_before_first_event_resubmits_same_chat_when_missing(
    monkeypatch,
) -> None:
    calls = []
    payload = {
        "turn_id": "turn_001",
        "message": "hello",
        "metadata": {"cid": "cid_1", "sid": "sid_1"},
    }
    build_payload = AsyncMock(return_value=payload)
    status_probe = AsyncMock(side_effect=chat.TurnStatusRequestError(
        "turn not found",
        status_code=404,
    ))

    async def streaming(url, _headers, request_payload, _timeout):
        calls.append((url, request_payload))
        if len(calls) == 1:
            raise OSError("connection lost before first event")
        yield {
            "type": "turn.logical_settled",
            "turn_id": "turn_001",
            "event_seq": 1,
            "next_input": None,
        }

    _install_reconnect_stream(monkeypatch, streaming)
    monkeypatch.setattr(chat, "build_chat_payload", build_payload)
    monkeypatch.setattr(chat, "get_turn_status", status_probe)

    event_stream = chat.stream_chat({}, "hello", [])
    events = [event async for event in event_stream]

    assert len(events) == 1
    assert isinstance(events[0], TurnLogicalSettledEvent)
    assert [url for url, _payload in calls] == [
        "https://example.com/mind-chat",
        "https://example.com/mind-chat",
    ]
    assert calls[0][1] is payload
    assert calls[1][1] is payload
    build_payload.assert_awaited_once()
    status_probe.assert_awaited_once_with(
        cid="cid_1",
        sid="sid_1",
        turn_id="turn_001",
    )


@pytest.mark.anyio
async def test_disconnect_before_first_event_attaches_when_turn_exists(
    monkeypatch,
) -> None:
    calls = []
    status_probe = AsyncMock(return_value=object())

    async def streaming(url, _headers, payload, _timeout):
        calls.append((url, payload))
        if url.endswith("/mind-chat"):
            raise OSError("response headers were lost")
        yield {
            "type": "turn.logical_settled",
            "turn_id": "turn_001",
            "event_seq": 3,
            "next_input": None,
        }

    _install_reconnect_stream(monkeypatch, streaming)
    monkeypatch.setattr(chat, "get_turn_status", status_probe)

    event_stream = chat.stream_chat({}, "hello", [])
    events = [event async for event in event_stream]

    assert len(events) == 1
    assert isinstance(events[0], TurnLogicalSettledEvent)
    assert [url for url, _payload in calls] == [
        "https://example.com/mind-chat",
        "https://example.com/mind-attach",
    ]
    assert calls[1][1] == {
        "cid": "cid_1",
        "sid": "sid_1",
        "turn_id": "turn_001",
        "after_seq": 0,
    }
    status_probe.assert_awaited_once()


@pytest.mark.anyio
async def test_fatal_status_probe_error_stops_pre_event_recovery(
    monkeypatch,
) -> None:
    calls = []
    status_probe = AsyncMock(side_effect=chat.TurnStatusRequestError(
        "turn status unauthorized",
        status_code=401,
    ))

    async def streaming(url, _headers, _payload, _timeout):
        calls.append(url)
        raise OSError("connection lost before first event")
        yield

    _install_reconnect_stream(monkeypatch, streaming)
    monkeypatch.setattr(chat, "get_turn_status", status_probe)

    event_stream = chat.stream_chat({}, "hello", [])

    with pytest.raises(chat.TurnStatusRequestError) as raised:
        async for _event in event_stream:
            pass

    assert raised.value.status_code == 401
    assert calls == ["https://example.com/mind-chat"]
    assert event_stream.end_reason == "fatal"


@pytest.mark.anyio
@pytest.mark.parametrize("status_code", [401, 403, 404, 409, 422])
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
    assert event_stream.end_reason == "fatal"


@pytest.mark.anyio
async def test_ping_resets_attach_backoff(monkeypatch) -> None:
    calls = []

    async def streaming(url, _headers, _payload, _timeout):
        calls.append(url)
        if url.endswith("/mind-chat"):
            yield {
                "type": "text.delta",
                "turn_id": "turn_001",
                "event_seq": 1,
                "segment_id": "segment_1",
                "text": "started",
            }
            raise OSError("connection lost")

        attach_count = len(calls) - 1
        if attach_count < 5:
            yield {"type": "ping"}
            raise OSError("connection lost")
        yield {
            "type": "turn.logical_settled",
            "turn_id": "turn_001",
            "event_seq": 2,
            "next_input": None,
        }

    monkeypatch.setattr(
        chat,
        "ATTACH_BACKOFF_DELAYS_SEC",
        (0.0, 0.0, 0.0),
    )
    _install_reconnect_stream(monkeypatch, streaming)

    event_stream = chat.stream_chat({}, "hello", [])
    events = [event async for event in event_stream]

    assert [event.event_seq for event in events] == [1, 2]
    assert len(calls) == 6
    assert event_stream.end_reason == "settled"


@pytest.mark.anyio
async def test_attach_retries_beyond_previous_budget_until_settled(
    monkeypatch,
) -> None:
    calls = []

    async def streaming(url, _headers, _payload, _timeout):
        calls.append(url)
        if url.endswith("/mind-chat"):
            yield {
                "type": "turn.done",
                "turn_id": "turn_001",
                "event_seq": 1,
                "status": "completed",
            }
            raise OSError("connection lost")

        if len(calls) < 7:
            raise OSError("connection lost")
        yield {
            "type": "turn.logical_settled",
            "turn_id": "turn_001",
            "event_seq": 2,
            "next_input": None,
        }

    monkeypatch.setattr(
        chat,
        "ATTACH_BACKOFF_DELAYS_SEC",
        (0.0, 0.0, 0.0),
    )
    _install_reconnect_stream(monkeypatch, streaming)

    event_stream = chat.stream_chat({}, "hello", [])
    events = [event async for event in event_stream]

    assert [event.event_seq for event in events] == [1, 2]
    assert len(calls) == 7
    assert event_stream.end_reason == "settled"


@pytest.mark.anyio
async def test_duplicate_replay_keeps_backoff_and_caps_delay(
    monkeypatch,
) -> None:
    calls = []
    waits = []

    async def streaming(url, _headers, payload, _timeout):
        calls.append((url, payload))
        if url.endswith("/mind-chat"):
            yield {
                "type": "text.delta",
                "turn_id": "turn_001",
                "event_seq": 1,
                "segment_id": "segment_1",
                "text": "first",
            }
            raise OSError("connection lost")

        attach_count = len(calls) - 1
        if attach_count < 7:
            yield {
                "type": "text.delta",
                "turn_id": "turn_001",
                "event_seq": 1,
                "segment_id": "segment_1",
                "text": "duplicate",
            }
            raise OSError("connection lost")
        yield {
            "type": "turn.logical_settled",
            "turn_id": "turn_001",
            "event_seq": 2,
            "next_input": None,
        }

    async def record_wait(delay):
        waits.append(delay)

    monkeypatch.setattr(chat, "ATTACH_BACKOFF_JITTER_RATIO", 0.0)
    monkeypatch.setattr(
        chat.TurnEventStream,
        "_wait_before_attach",
        staticmethod(record_wait),
    )
    _install_reconnect_stream(monkeypatch, streaming)

    event_stream = chat.stream_chat({}, "hello", [])
    events = [event async for event in event_stream]

    assert [event.event_seq for event in events] == [1, 2]
    assert len(calls) == 8
    assert all(payload["after_seq"] == 1 for _, payload in calls[1:])
    assert waits == [0.0, 0.2, 0.5, 1.0, 2.0, 5.0, 5.0]
    assert event_stream.end_reason == "settled"


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
