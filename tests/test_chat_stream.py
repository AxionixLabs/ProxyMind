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
from mind_nova.tool_approval import ToolApprovalSnapshot


@pytest.fixture(autouse=True)
def current_wire_envelope(monkeypatch) -> None:
    """让伪传输使用当前 mind.chat 持久事件 envelope。"""
    parser = chat.parse_stream_event

    def parse(payload):
        current = dict(payload)
        if str(current.get("type") or "") != "ping":
            current.setdefault("proto", "mind.chat")
            current.setdefault("cid", "cid_1")
            current.setdefault("sid", "sid_1")
            current.setdefault("presentation_epoch", 1)
            if str(current.get("type") or "").startswith("text."):
                current.setdefault("segment_id", "segment_test")
        return parser(current)

    monkeypatch.setattr(chat, "parse_stream_event", parse)


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
    monkeypatch.setattr(chat, "build_chat_payload", AsyncMock(return_value={
        "turn_id": "turn_1",
        "metadata": {"cid": "cid_1", "sid": "sid_1"},
    }))
    monkeypatch.setattr(chat, "build_service_headers", Mock(return_value={}))
    monkeypatch.setattr(chat.service_endpoints, "endpoint", Mock(return_value="url"))
    monkeypatch.setattr(chat, "streaming", Mock(return_value=payload_stream))
    return payload_stream


def _install_reconnect_stream(monkeypatch, transport) -> None:
    monkeypatch.setattr(chat, "build_chat_payload", AsyncMock(return_value={
        "turn_id": "turn_001",
        "metadata": {"cid": "cid_1", "sid": "sid_1"},
    }))
    monkeypatch.setattr(chat, "build_service_headers", Mock(return_value={
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
            "event_seq": 1,
            "segment_id": "segment-1",
            "text": "answer",
        }
        yield {
            "type": "turn.logical_settled",
            "turn_id": "turn_001",
            "event_seq": 2,
            "next_input": None,
        }

    build_payload = AsyncMock(return_value={
        "turn_id": "turn_001",
        "message": "hello",
        "metadata": {"cid": "cid_1", "sid": "sid_1"},
    })
    make_headers = Mock(return_value={"authorization": "test"})
    endpoint = Mock(return_value="https://example.com/chat")
    monkeypatch.setattr(chat, "build_chat_payload", build_payload)
    monkeypatch.setattr(chat, "build_service_headers", make_headers)
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
        yield {
            "type": "turn.failed",
            "turn_id": "turn_1",
            "event_seq": 1,
            "error": "timeout",
        }
        await release.wait()
        yield {
            "type": "turn.logical_settled",
            "turn_id": "turn_1",
            "event_seq": 2,
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
        yield {"type": "turn.done", "turn_id": "turn_1", "event_seq": 1}
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
        yield {"type": "turn.done", "turn_id": "turn_1", "event_seq": 1}
        yield {
            "type": "turn.logical_settled",
            "turn_id": "turn_1",
            "event_seq": 2,
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
async def test_cancellation_clears_reconnecting_status(monkeypatch) -> None:
    reconnecting = []
    reconnect_started = asyncio.Event()

    async def streaming(_url, _headers, _payload, _timeout):
        raise OSError("connection lost")
        yield

    async def wait_before_attach(_delay):
        reconnect_started.set()
        await asyncio.Future()

    _install_reconnect_stream(monkeypatch, streaming)
    monkeypatch.setattr(
        chat.TurnEventStream,
        "_wait_before_attach",
        staticmethod(wait_before_attach),
    )
    event_stream = chat.stream_chat(
        {},
        "hello",
        [],
        on_reconnect_status=reconnecting.append,
    )

    consuming = asyncio.create_task(anext(event_stream.__aiter__()))
    await reconnect_started.wait()
    consuming.cancel()
    with pytest.raises(asyncio.CancelledError):
        await consuming

    assert event_stream.end_reason == "cancelled"
    assert reconnecting == [True, False]


@pytest.mark.anyio
async def test_consecutive_attach_failures_stop_after_retry_budget(monkeypatch) -> None:
    event_stream = chat.stream_chat({}, "hello", [])
    event_stream._attach_target = {
        "cid": "cid_1",
        "sid": "sid_1",
        "turn_id": "turn_001",
    }
    event_stream._reconnect_started_at = 100.0

    monkeypatch.setattr(chat.time, "monotonic", lambda: 161.0)
    transport = Mock()
    monkeypatch.setattr(chat, "streaming", transport)

    assert await event_stream._resume_stream(OSError("still offline")) is False
    transport.assert_not_called()


def test_valid_event_resets_consecutive_attach_budget(monkeypatch) -> None:
    reconnecting = []
    monkeypatch.setattr(chat, "TRANSPORT_RETRY_MIN_VISIBLE_SEC", 0.0)
    event_stream = chat.stream_chat(
        {},
        "hello",
        [],
        on_reconnect_status=reconnecting.append,
    )
    event_stream._reconnect_failures = 4
    event_stream._reconnect_started_at = 100.0
    event_stream._set_reconnecting(True)

    event_stream._mark_transport_healthy()

    assert event_stream._reconnect_failures == 0
    assert event_stream._reconnect_started_at is None
    assert reconnecting == [True, False]


def test_transport_retry_status_has_minimum_visible_interval(monkeypatch) -> None:
    now = [100.0]
    scheduled = {}
    reconnecting = []

    class Handle:
        cancelled_value = False

        def cancel(self) -> None:
            self.cancelled_value = True

        def cancelled(self) -> bool:
            return self.cancelled_value

    handle = Handle()
    loop = Mock()

    def call_later(delay, callback, *args):
        scheduled.update(delay=delay, callback=callback, args=args)
        return handle

    loop.call_later.side_effect = call_later
    monkeypatch.setattr(chat.time, "monotonic", lambda: now[0])
    monkeypatch.setattr(chat.asyncio, "get_running_loop", lambda: loop)

    event_stream = chat.stream_chat(
        {},
        "hello",
        [],
        on_reconnect_status=reconnecting.append,
    )
    event_stream._set_reconnecting(True)

    now[0] = 100.1
    event_stream._mark_transport_healthy()

    assert reconnecting == [True]
    assert scheduled["delay"] == pytest.approx(0.7)

    scheduled["callback"](*scheduled["args"])

    assert reconnecting == [True, False]


def test_stale_retry_clear_does_not_hide_new_reconnect(monkeypatch) -> None:
    now = [100.0]
    scheduled = []
    reconnecting = []

    class Handle:
        cancelled_value = False

        def cancel(self) -> None:
            self.cancelled_value = True

        def cancelled(self) -> bool:
            return self.cancelled_value

    loop = Mock()

    def call_later(delay, callback, *args):
        handle = Handle()
        scheduled.append((delay, callback, args, handle))
        return handle

    loop.call_later.side_effect = call_later
    monkeypatch.setattr(chat.time, "monotonic", lambda: now[0])
    monkeypatch.setattr(chat.asyncio, "get_running_loop", lambda: loop)

    event_stream = chat.stream_chat(
        {},
        "hello",
        [],
        on_reconnect_status=reconnecting.append,
    )
    event_stream._set_reconnecting(True)

    now[0] = 100.1
    event_stream._mark_transport_healthy()
    _, stale_callback, stale_args, stale_handle = scheduled[-1]

    now[0] = 100.2
    event_stream._set_reconnecting(True)
    stale_callback(*stale_args)

    assert stale_handle.cancelled() is True
    assert reconnecting == [True]
    assert event_stream._reconnecting is True


@pytest.mark.anyio
async def test_silent_stream_timeout_attaches_and_reports_reconnecting(
    monkeypatch,
) -> None:
    calls = []
    reconnecting = []

    async def streaming(url, _headers, _payload, _timeout):
        calls.append(url)
        if url.endswith("/mind-chat"):
            yield {
                "type": "text.delta",
                "turn_id": "turn_001",
                "event_seq": 1,
                "segment_id": "segment_1",
                "text": "partial",
            }
            await asyncio.Event().wait()

        yield {
            "type": "turn.logical_settled",
            "turn_id": "turn_001",
            "event_seq": 2,
            "next_input": None,
        }

    _install_reconnect_stream(monkeypatch, streaming)
    monkeypatch.setattr(chat, "STREAM_PAYLOAD_SILENCE_TIMEOUT_SEC", 0.01)
    event_stream = chat.stream_chat(
        {},
        "hello",
        [],
        timeout=1.0,
        on_reconnect_status=reconnecting.append,
    )

    events = [event async for event in event_stream]

    assert [type(event) for event in events] == [
        TextDeltaEvent,
        TurnLogicalSettledEvent,
    ]
    assert calls == [
        "https://example.com/mind-chat",
        "https://example.com/mind-attach",
    ]
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
async def test_ping_before_first_event_still_probes_before_resubmit(
    monkeypatch,
) -> None:
    """验证基础设施 ping 不会冒充已持久化的业务事件。"""
    calls = []
    status_probe = AsyncMock(side_effect=chat.TurnStatusRequestError(
        "turn not found",
        status_code=404,
    ))

    async def streaming(url, _headers, payload, _timeout):
        calls.append((url, payload))
        if len(calls) == 1:
            yield {"type": "ping"}
            raise OSError("connection lost before first business event")
        yield {
            "type": "turn.logical_settled",
            "turn_id": "turn_001",
            "event_seq": 1,
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
        "https://example.com/mind-chat",
    ]
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
    monkeypatch.setattr(chat, "build_chat_payload", AsyncMock(return_value={
        "turn_id": "turn_001",
        "metadata": {"cid": "cid_1", "sid": "sid_1"},
    }))
    event_stream = chat.stream_chat({}, "hello", [])

    events = [event async for event in event_stream]

    assert len(events) == 1
    assert isinstance(events[0], TurnLogicalSettledEvent)
    assert event_stream.end_reason == "settled"
    assert payload_stream.closed is True


@pytest.mark.anyio
async def test_sequence_gap_attaches_from_last_confirmed_event(monkeypatch) -> None:
    calls = []

    async def streaming(url, _headers, _payload, _timeout):
        calls.append(url)
        if url.endswith("/mind-chat"):
            yield {
                "type": "text.delta",
                "turn_id": "turn_001",
                "event_seq": 1,
                "segment_id": "segment_1",
                "text": "one",
            }
            yield {
                "type": "text.delta",
                "turn_id": "turn_001",
                "event_seq": 3,
                "segment_id": "segment_1",
                "text": "must not leak",
            }
        else:
            yield {
                "type": "text.delta",
                "turn_id": "turn_001",
                "event_seq": 2,
                "segment_id": "segment_1",
                "text": "two",
            }
            yield {
                "type": "text.delta",
                "turn_id": "turn_001",
                "event_seq": 3,
                "segment_id": "segment_1",
                "text": "three",
            }
            yield {
                "type": "turn.logical_settled",
                "turn_id": "turn_001",
                "event_seq": 4,
                "next_input": None,
            }

    _install_reconnect_stream(monkeypatch, streaming)

    events = [event async for event in chat.stream_chat({}, "hello", [])]

    assert [event.event_seq for event in events] == [1, 2, 3, 4]
    assert [
        event.text for event in events if isinstance(event, TextDeltaEvent)
    ] == ["one", "two", "three"]
    assert calls == [
        "https://example.com/mind-chat",
        "https://example.com/mind-attach",
    ]


@pytest.mark.anyio
async def test_attach_restores_approval_snapshot_before_replay(monkeypatch) -> None:
    calls = []
    restored = []
    snapshot = ToolApprovalSnapshot(
        cid="cid_1",
        sid="sid_1",
        turn_id="turn_001",
        turn_status="waiting_approval",
        turn_settled=False,
        last_event_seq=4,
        approvals=(),
    )

    async def restore(value):
        restored.append(value)

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
        yield {
            "type": "turn.logical_settled",
            "turn_id": "turn_001",
            "event_seq": 2,
            "next_input": None,
        }

    _install_reconnect_stream(monkeypatch, streaming)
    monkeypatch.setattr(
        chat,
        "reconcile_tool_approval_snapshot",
        AsyncMock(return_value=snapshot),
    )

    events = [
        event async for event in chat.stream_chat(
            {},
            "hello",
            [],
            on_approval_snapshot=restore,
        )
    ]

    assert [event.event_seq for event in events] == [1, 2]
    assert restored == [snapshot]
    chat.reconcile_tool_approval_snapshot.assert_awaited_once_with(
        cid="cid_1",
        sid="sid_1",
        turn_id="turn_001",
    )
    assert calls == [
        "https://example.com/mind-chat",
        "https://example.com/mind-attach",
    ]


@pytest.mark.anyio
async def test_snapshot_failure_does_not_block_event_attach(monkeypatch) -> None:
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
        yield {
            "type": "turn.logical_settled",
            "turn_id": "turn_001",
            "event_seq": 2,
            "next_input": None,
        }

    _install_reconnect_stream(monkeypatch, streaming)
    monkeypatch.setattr(
        chat,
        "reconcile_tool_approval_snapshot",
        AsyncMock(side_effect=chat.ToolApprovalSnapshotRequestError(
            "snapshot unavailable",
            status_code=503,
        )),
    )

    events = [
        event async for event in chat.stream_chat(
            {},
            "hello",
            [],
            on_approval_snapshot=AsyncMock(),
        )
    ]

    assert [event.event_seq for event in events] == [1, 2]
    assert calls == [
        "https://example.com/mind-chat",
        "https://example.com/mind-attach",
    ]


@pytest.mark.anyio
@pytest.mark.parametrize(
    "foreign_identity",
    (
        {"cid": "cid_other", "turn_id": "turn_001"},
        {"sid": "sid_other", "turn_id": "turn_001"},
        {"turn_id": "turn_other"},
    ),
)
async def test_foreign_turn_event_is_rejected_before_delivery(
    monkeypatch,
    foreign_identity,
) -> None:
    async def streaming(_url, _headers, _payload, _timeout):
        yield {
            "type": "tool.call",
            **foreign_identity,
            "event_seq": 1,
                "call_id": "call_1",
                "name": "shell_command",
                "arguments": {},
                "reason": "模型需要调用 shell。",
        }

    _install_reconnect_stream(monkeypatch, streaming)
    event_stream = chat.stream_chat({}, "hello", [])

    with pytest.raises(ValueError, match="current turn"):
        async for _event in event_stream:
            pass

    assert event_stream.end_reason == "protocol_error"
