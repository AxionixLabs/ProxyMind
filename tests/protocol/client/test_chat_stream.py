# -*- coding: utf-8 -*-

"""验证聊天流协议的增量解析、错误与终止语义。

这些场景共享同一流式解析状态机，维持整体可保留跨分片的不变量。
"""

import asyncio
from types import SimpleNamespace
from unittest.mock import (
    AsyncMock,
    Mock,
)

import httpx
import pytest

from protocol.client import chat
from protocol.schema.stream_events import (
    ContextCompactionEvent,
    StreamGapEvent,
    TextDeltaEvent,
    TurnCompletedEvent,
)
from protocol.schema.tool_approval import ToolApprovalSnapshot


def _recovery_recorder(events: list[tuple[str, int]]):
    """返回记录传输恢复阶段和水位的异步回调。"""
    async def record(phase: str, event_seq: int) -> None:
        events.append((phase, event_seq))

    return record


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
        if str(current.get("type") or "") == "turn.completed":
            current.setdefault("status", "completed")
            current.setdefault("last_event_seq", current.get("event_seq"))
            current.setdefault("completed_at", 1.0)
        if str(current.get("type") or "").startswith("text."):
            current.setdefault("segment_id", "segment_test")
            current.setdefault("item_id", current["segment_id"])
            current.setdefault("item_kind", "text")
            current.setdefault(
                "item_status",
                "in_progress"
                if current["type"] == "text.delta"
                else "completed",
            )
        if str(current.get("type") or "") == "tool.call":
            current.setdefault("item_id", current.get("call_id"))
            current.setdefault("item_kind", "tool_call")
            current.setdefault("item_status", "waiting_result")
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


def _install_reconnect_stream(
    monkeypatch,
    transport,
    *,
    replay_target_seq: int = 2,
) -> None:
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
    monkeypatch.setattr(
        chat,
        "get_turn_status",
        AsyncMock(return_value=SimpleNamespace(
            last_event_seq=replay_target_seq,
            terminal=None,
        )),
    )


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
            "type": "turn.completed",
            "turn_id": "turn_001",
            "event_seq": 2,
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
    assert isinstance(events[1], TurnCompletedEvent)
    assert events[1].turn_id == "turn_001"
    build_payload.assert_awaited_once()
    endpoint.assert_called_once_with("/mind-chat")
    make_headers.assert_called_once_with()


@pytest.mark.anyio
async def test_compaction_events_continue_same_turn_without_new_request(
    monkeypatch,
) -> None:
    async def streaming(*_args, **_kwargs):
        yield {
            "type": "turn.started",
            "turn_id": "turn_001",
            "event_seq": 1,
        }
        yield {
            "type": "context.compaction.started",
            "turn_id": "turn_001",
            "event_seq": 2,
            "item_id": "compaction_1",
            "item_kind": "context_compaction",
            "item_status": "in_progress",
            "phase": "mid_turn",
            "trigger": "automatic",
            "reason": "context_limit",
        }
        yield {
            "type": "context.compaction.completed",
            "turn_id": "turn_001",
            "event_seq": 3,
            "item_id": "compaction_1",
            "item_kind": "context_compaction",
            "item_status": "completed",
            "phase": "mid_turn",
            "trigger": "automatic",
            "reason": "context_limit",
            "replacement_version": 2,
        }
        yield {
            "type": "text.delta",
            "turn_id": "turn_001",
            "event_seq": 4,
            "segment_id": "segment_1",
            "text": "continued",
        }
        yield {
            "type": "turn.completed",
            "turn_id": "turn_001",
            "event_seq": 5,
        }

    build_payload = AsyncMock(return_value={
        "turn_id": "turn_001",
        "message": "hello",
        "metadata": {"cid": "cid_1", "sid": "sid_1"},
    })
    endpoint = Mock(return_value="https://example.com/mind-chat")
    transport = Mock(return_value=streaming())
    monkeypatch.setattr(chat, "build_chat_payload", build_payload)
    monkeypatch.setattr(chat, "build_service_headers", Mock(return_value={}))
    monkeypatch.setattr(chat.service_endpoints, "endpoint", endpoint)
    monkeypatch.setattr(chat, "streaming", transport)

    events = await _collect(chat.stream_chat({}, "hello", []))

    assert [event.type for event in events] == [
        "turn.started",
        "context.compaction.started",
        "context.compaction.completed",
        "text.delta",
        "turn.completed",
    ]
    assert all(event.turn_id == "turn_001" for event in events)
    assert isinstance(events[1], ContextCompactionEvent)
    assert isinstance(events[2], ContextCompactionEvent)
    build_payload.assert_awaited_once()
    endpoint.assert_called_once_with("/mind-chat")
    transport.assert_called_once()


@pytest.mark.anyio
async def test_stream_chat_continues_from_session_event_watermark(monkeypatch) -> None:
    async def payloads():
        yield {
            "type": "text.delta",
            "turn_id": "turn_1",
            "event_seq": 11,
            "text": "next turn",
        }
        yield {
            "type": "turn.completed",
            "turn_id": "turn_1",
            "event_seq": 12,
        }

    _install_stream(monkeypatch, payloads())
    event_stream = chat.stream_chat(
        {},
        "hello",
        [],
        initial_event_seq=10,
    )

    events = await _collect(event_stream)

    assert [event.event_seq for event in events] == [11, 12]
    assert event_stream.last_event_seq == 12


@pytest.mark.anyio
async def test_observe_turn_attaches_without_submitting_chat(monkeypatch) -> None:
    calls = []

    async def streaming(url, _headers, payload, _timeout):
        calls.append((url, payload))
        yield {
            "type": "turn.started",
            "turn_id": "turn_existing",
            "event_seq": 3,
        }
        yield {
            "type": "turn.completed",
            "turn_id": "turn_existing",
            "event_seq": 4,
        }

    build_payload = AsyncMock(side_effect=AssertionError("must not submit"))
    monkeypatch.setattr(chat, "build_chat_payload", build_payload)
    monkeypatch.setattr(chat, "build_service_headers", Mock(return_value={}))
    monkeypatch.setattr(
        chat.service_endpoints,
        "endpoint",
        lambda path: f"https://example.com{path}",
    )
    monkeypatch.setattr(chat, "streaming", streaming)

    event_stream = chat.observe_turn(
        cid="cid_1",
        sid="sid_1",
        turn_id="turn_existing",
        initial_event_seq=1,
    )
    events = await _collect(event_stream)

    assert [event.event_seq for event in events] == [3, 4]
    assert calls == [(
        "https://example.com/mind-attach",
        {
            "cid": "cid_1",
            "sid": "sid_1",
            "turn_id": "turn_existing",
            "after_seq": 1,
        },
    )]
    build_payload.assert_not_awaited()


@pytest.mark.anyio
async def test_cold_observe_reports_replay_until_terminal_target(monkeypatch) -> None:
    recovery = []

    async def payloads():
        yield {
            "type": "turn.started",
            "turn_id": "turn_existing",
            "event_seq": 1,
        }
        yield {
            "type": "turn.completed",
            "turn_id": "turn_existing",
            "event_seq": 2,
        }

    _install_stream(monkeypatch, payloads())
    event_stream = chat.observe_turn(
        cid="cid_1",
        sid="sid_1",
        turn_id="turn_existing",
        initial_event_seq=0,
        replay_target_seq=2,
        on_recovery_status=_recovery_recorder(recovery),
    )

    assert [event.event_seq for event in await _collect(event_stream)] == [1, 2]
    assert recovery == [
        ("replaying", 0),
        ("caught_up", 2),
    ]


@pytest.mark.anyio
async def test_retained_prefix_gap_advances_replay_floor(monkeypatch) -> None:
    async def payloads():
        yield {
            "type": "stream.gap",
            "cid": "cid_1",
            "sid": "sid_1",
            "turn_id": "turn_1",
            "gap_kind": "retained_prefix",
            "requested_after_seq": 1,
            "first_event_seq": 5,
            "next_seq": 4,
        }
        yield {
            "type": "turn.completed",
            "turn_id": "turn_1",
            "event_seq": 5,
        }

    _install_stream(monkeypatch, payloads())
    event_stream = chat.stream_chat(
        {},
        "hello",
        [],
        initial_event_seq=1,
    )

    events = await _collect(event_stream)

    assert [event.type for event in events] == [
        "stream.gap",
        "turn.completed",
    ]
    assert event_stream.last_event_seq == 5


@pytest.mark.anyio
async def test_internal_gap_waits_for_terminal_snapshot(monkeypatch) -> None:
    calls = []

    async def streaming(url, _headers, _payload, _timeout):
        calls.append(url)
        yield {
            "type": "stream.gap",
            "cid": "cid_1",
            "sid": "sid_1",
            "turn_id": "turn_001",
            "gap_kind": "internal",
            "requested_after_seq": 1,
            "expected_event_seq": 2,
            "observed_event_seq": 3,
            "retryable": True,
        }

    _install_reconnect_stream(monkeypatch, streaming, replay_target_seq=4)
    monkeypatch.setattr(chat, "get_turn_status", AsyncMock(return_value=SimpleNamespace(
        last_event_seq=4,
        terminal=SimpleNamespace(
            type="turn.completed",
            turn_id="turn_001",
            status="completed",
            error="",
            last_event_seq=4,
            completed_at=1.0,
            duration_ms=4_000,
        ),
    )))
    event_stream = chat.stream_chat(
        {},
        "hello",
        [],
        initial_event_seq=1,
    )

    events = await _collect(event_stream)

    assert len(events) == 2
    assert isinstance(events[0], StreamGapEvent)
    assert events[0].gap_kind == "internal"
    assert isinstance(events[1], TurnCompletedEvent)
    assert event_stream.last_event_seq == 4
    assert event_stream.end_reason == "settled"
    assert calls == ["https://example.com/mind-chat"]


@pytest.mark.anyio
async def test_failed_completion_closes_without_waiting_for_eof(monkeypatch) -> None:
    async def payloads():
        yield {
            "type": "turn.completed",
            "status": "failed",
            "turn_id": "turn_1",
            "event_seq": 1,
            "error": "timeout",
        }
        await asyncio.Event().wait()

    payload_stream = _install_stream(monkeypatch, payloads())
    event_stream = chat.stream_chat({}, "hello", [])
    events = await asyncio.wait_for(_collect(event_stream), timeout=1.0)

    assert len(events) == 1
    assert isinstance(events[0], TurnCompletedEvent)
    assert events[0].status == "failed"
    assert event_stream.end_reason == "settled"
    assert payload_stream.closed is True


@pytest.mark.anyio
async def test_completed_terminal_closes_stream_immediately(monkeypatch) -> None:
    async def payloads():
        yield {"type": "turn.completed", "turn_id": "turn_1", "event_seq": 1}
        await asyncio.Event().wait()

    payload_stream = _install_stream(monkeypatch, payloads())
    event_stream = chat.stream_chat({}, "hello", [])
    events = await asyncio.wait_for(_collect(event_stream), timeout=1.0)

    assert len(events) == 1
    assert isinstance(events[0], TurnCompletedEvent)
    assert event_stream.end_reason == "settled"
    assert payload_stream.closed is True


@pytest.mark.anyio
async def test_single_terminal_closes_stream_without_waiting_for_eof(monkeypatch) -> None:
    async def payloads():
        yield {"type": "turn.completed", "turn_id": "turn_1", "event_seq": 1}
        await asyncio.Event().wait()

    payload_stream = _install_stream(monkeypatch, payloads())
    event_stream = chat.stream_chat({}, "hello", [])

    events = [event async for event in event_stream]

    assert [type(event) for event in events] == [TurnCompletedEvent]
    assert event_stream.end_reason == "settled"
    assert payload_stream.closed is True


@pytest.mark.anyio
async def test_terminal_prevents_late_transport_failure_from_reopening_turn(monkeypatch) -> None:
    calls = []
    reconnecting = []

    async def streaming(url, _headers, _payload, _timeout):
        calls.append(url)
        if url.endswith("/mind-chat"):
            yield {
                "type": "turn.completed",
                "status": "failed",
                "turn_id": "turn_001",
                "event_seq": 1,
                "error": "upstream",
            }
            raise OSError("connection lost")

    _install_reconnect_stream(monkeypatch, streaming)
    event_stream = chat.stream_chat(
        {},
        "hello",
        [],
        on_recovery_status=_recovery_recorder(reconnecting),
    )

    events = [event async for event in event_stream]

    assert len(events) == 1
    assert isinstance(events[0], TurnCompletedEvent)
    assert event_stream.end_reason == "settled"
    assert calls == ["https://example.com/mind-chat"]
    assert reconnecting == []


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
        on_recovery_status=_recovery_recorder(reconnecting),
    )

    consuming = asyncio.create_task(anext(event_stream.__aiter__()))
    await reconnect_started.wait()
    consuming.cancel()
    with pytest.raises(asyncio.CancelledError):
        await consuming

    assert event_stream.end_reason == "cancelled"
    assert reconnecting == [
        ("reconnecting", 0),
        ("closed", 0),
    ]


@pytest.mark.anyio
async def test_control_probe_wakes_recovery_backoff(monkeypatch) -> None:
    wait_started = asyncio.Event()
    wait_cancelled = asyncio.Event()

    async def wait_before_attach(_delay):
        wait_started.set()
        try:
            await asyncio.Future()
        finally:
            wait_cancelled.set()

    monkeypatch.setattr(
        chat.TurnEventStream,
        "_wait_before_attach",
        staticmethod(wait_before_attach),
    )
    event_stream = chat.stream_chat({}, "hello", [])
    waiting = asyncio.create_task(event_stream._wait_for_recovery_delay(5.0))
    await wait_started.wait()

    event_stream.request_recovery_probe()
    await asyncio.wait_for(waiting, timeout=0.1)

    assert wait_cancelled.is_set()
    assert event_stream._control_settlement_probe_active is True


@pytest.mark.anyio
async def test_recovery_callback_failure_still_closes_payload_stream() -> None:
    """验证展示回调失败不能泄漏底层 HTTP 流。"""
    async def payloads():
        await asyncio.Event().wait()
        yield {}

    async def fail_recovery(_phase: str, _event_seq: int) -> None:
        raise RuntimeError("recovery sink failed")

    payload_stream = _PayloadStream(payloads())
    event_stream = chat.stream_chat(
        {},
        "hello",
        [],
        on_recovery_status=fail_recovery,
    )
    event_stream._payload_stream = payload_stream
    event_stream._recovery_phase = "reconnecting"

    with pytest.raises(RuntimeError, match="recovery sink failed"):
        await event_stream.aclose()

    assert payload_stream.closed is True


@pytest.mark.anyio
async def test_recovery_probe_observes_terminal_without_silence_timeout(
    monkeypatch,
) -> None:
    first_event_sent = asyncio.Event()
    calls: list[str] = []
    recovery_phases: list[tuple[str, int]] = []

    async def streaming(url, _headers, _payload, _timeout):
        calls.append(url)
        yield {
            "type": "turn.started",
            "turn_id": "turn_001",
            "event_seq": 1,
        }
        first_event_sent.set()
        await asyncio.Event().wait()

    terminal = SimpleNamespace(
        type="turn.completed",
        turn_id="turn_001",
        status="interrupted",
        error=None,
        last_event_seq=2,
        completed_at=10.0,
        duration_ms=2_000,
    )
    _install_reconnect_stream(monkeypatch, streaming)
    status_probe = AsyncMock(return_value=SimpleNamespace(
        last_event_seq=2,
        terminal=terminal,
    ))
    monkeypatch.setattr(chat, "get_turn_status", status_probe)
    event_stream = chat.stream_chat(
        {},
        "hello",
        [],
        timeout=60.0,
        on_recovery_status=_recovery_recorder(recovery_phases),
    )

    collecting = asyncio.create_task(_collect(event_stream))
    await first_event_sent.wait()
    event_stream.request_recovery_probe()
    events = await asyncio.wait_for(collecting, timeout=0.2)

    assert [event.type for event in events] == [
        "turn.started",
        "turn.completed",
    ]
    assert events[-1].status == "interrupted"
    assert calls == ["https://example.com/mind-chat"]
    status_probe.assert_awaited_once()
    assert recovery_phases == [
        ("replaying", 1),
        ("caught_up", 2),
    ]


@pytest.mark.anyio
async def test_control_probe_closes_existing_transport_retry(
    monkeypatch,
) -> None:
    recovery_phases: list[tuple[str, int]] = []
    terminal = SimpleNamespace(
        type="turn.completed",
        turn_id="turn_001",
        status="interrupted",
        error=None,
        last_event_seq=2,
        completed_at=10.0,
        duration_ms=2_000,
    )
    _install_reconnect_stream(monkeypatch, Mock())
    monkeypatch.setattr(
        chat,
        "get_turn_status",
        AsyncMock(return_value=SimpleNamespace(
            last_event_seq=2,
            terminal=terminal,
        )),
    )
    event_stream = chat.stream_chat(
        {},
        "hello",
        [],
        on_recovery_status=_recovery_recorder(recovery_phases),
    )
    event_stream._attach_target = {
        "cid": "cid_1",
        "sid": "sid_1",
        "turn_id": "turn_001",
    }
    event_stream._response_observed = True
    event_stream._recovery_phase = "reconnecting"
    event_stream.request_recovery_probe()

    assert await event_stream._resume_stream()

    assert recovery_phases == [
        ("closed", 0),
        ("replaying", 0),
    ]


@pytest.mark.anyio
async def test_control_probe_waits_for_terminal_after_active_snapshot(
    monkeypatch,
) -> None:
    first_event_sent = asyncio.Event()
    calls: list[str] = []

    async def streaming(url, _headers, _payload, _timeout):
        calls.append(url)
        if url.endswith("/mind-chat"):
            yield {
                "type": "turn.started",
                "turn_id": "turn_001",
                "event_seq": 1,
            }
            first_event_sent.set()
            await asyncio.Event().wait()
        raise AssertionError("control settlement must not open an attach stream")

    active = SimpleNamespace(last_event_seq=1, terminal=None)
    terminal = SimpleNamespace(
        type="turn.completed",
        turn_id="turn_001",
        status="interrupted",
        error=None,
        last_event_seq=2,
        completed_at=10.0,
        duration_ms=2_000,
    )
    settled = SimpleNamespace(last_event_seq=2, terminal=terminal)
    _install_reconnect_stream(monkeypatch, streaming)
    monkeypatch.setattr(
        chat,
        "CONTROL_SETTLEMENT_PROBE_INTERVAL_SEC",
        0.01,
    )
    status_probe = AsyncMock(side_effect=(active, settled))
    monkeypatch.setattr(chat, "get_turn_status", status_probe)
    event_stream = chat.stream_chat({}, "hello", [], timeout=60.0)

    collecting = asyncio.create_task(_collect(event_stream))
    await first_event_sent.wait()
    event_stream.request_recovery_probe()
    events = await asyncio.wait_for(collecting, timeout=0.2)

    assert [event.type for event in events] == [
        "turn.started",
        "turn.completed",
    ]
    assert calls == ["https://example.com/mind-chat"]
    assert status_probe.await_count == 2


@pytest.mark.anyio
async def test_control_settlement_reprobes_while_source_events_continue(
    monkeypatch,
) -> None:
    first_event_sent = asyncio.Event()
    calls: list[str] = []

    async def streaming(url, _headers, _payload, _timeout):
        calls.append(url)
        if url.endswith("/mind-attach"):
            assert _payload["after_seq"] == 1
            yield {
                "type": "turn.completed",
                "turn_id": "turn_001",
                "event_seq": 9,
                "status": "interrupted",
            }
            return
        yield {
            "type": "turn.started",
            "turn_id": "turn_001",
            "event_seq": 1,
        }
        first_event_sent.set()
        while True:
            await asyncio.sleep(0.001)
            yield {"type": "ping"}

    terminal = SimpleNamespace(
        type="turn.completed",
        turn_id="turn_001",
        status="interrupted",
        error=None,
        last_event_seq=9,
        completed_at=10.0,
        duration_ms=9_000,
    )
    active = SimpleNamespace(last_event_seq=1, terminal=None)
    settled = SimpleNamespace(last_event_seq=9, terminal=terminal)
    _install_reconnect_stream(monkeypatch, streaming)
    monkeypatch.setattr(
        chat,
        "CONTROL_SETTLEMENT_PROBE_INTERVAL_SEC",
        0.01,
    )
    status_probe = AsyncMock(side_effect=(active, settled))
    monkeypatch.setattr(chat, "get_turn_status", status_probe)
    event_stream = chat.stream_chat({}, "hello", [], timeout=60.0)

    collecting = asyncio.create_task(_collect(event_stream))
    await first_event_sent.wait()
    event_stream.request_recovery_probe()
    events = await asyncio.wait_for(collecting, timeout=0.2)

    assert [event.type for event in events] == [
        "turn.started",
        "turn.completed",
    ]
    assert events[-1].last_event_seq == 9
    assert event_stream.last_event_seq == 9
    assert calls == [
        "https://example.com/mind-chat",
        "https://example.com/mind-attach",
    ]
    assert status_probe.await_count == 2


@pytest.mark.anyio
async def test_control_probe_replays_unseen_events_before_terminal(
    monkeypatch,
) -> None:
    first_event_sent = asyncio.Event()
    calls: list[str] = []

    async def streaming(url, _headers, _payload, _timeout):
        calls.append(url)
        if url.endswith("/mind-attach"):
            assert _payload["after_seq"] == 1
            yield {
                "type": "text.delta",
                "turn_id": "turn_001",
                "event_seq": 6,
                "text": "persisted before interrupt",
            }
            yield {
                "type": "turn.completed",
                "turn_id": "turn_001",
                "event_seq": 7,
                "status": "interrupted",
                "presentation_epoch": 2,
            }
            return
        yield {
            "type": "turn.started",
            "turn_id": "turn_001",
            "event_seq": 1,
        }
        first_event_sent.set()
        await asyncio.Event().wait()

    terminal = SimpleNamespace(
        type="turn.completed",
        turn_id="turn_001",
        status="interrupted",
        error=None,
        last_event_seq=7,
        completed_at=10.0,
        duration_ms=7_000,
    )
    _install_reconnect_stream(monkeypatch, streaming)
    status_probe = AsyncMock(return_value=SimpleNamespace(
        last_event_seq=7,
        terminal=terminal,
    ))
    monkeypatch.setattr(chat, "get_turn_status", status_probe)
    event_stream = chat.stream_chat({}, "hello", [], timeout=60.0)

    collecting = asyncio.create_task(_collect(event_stream))
    await first_event_sent.wait()
    event_stream.request_recovery_probe()
    events = await asyncio.wait_for(collecting, timeout=0.2)

    assert [event.type for event in events] == [
        "turn.started",
        "text.delta",
        "turn.completed",
    ]
    assert events[-1].presentation_epoch == 2
    assert events[-1].last_event_seq == 7
    assert event_stream.last_event_seq == 7
    assert calls == [
        "https://example.com/mind-chat",
        "https://example.com/mind-attach",
    ]


@pytest.mark.anyio
async def test_attach_budget_rolls_over_without_releasing_active_turn(
    monkeypatch,
) -> None:
    event_stream = chat.stream_chat({}, "hello", [])
    event_stream._attach_target = {
        "cid": "cid_1",
        "sid": "sid_1",
        "turn_id": "turn_001",
    }
    event_stream._reconnect_started_at = 100.0

    monkeypatch.setattr(chat.time, "monotonic", lambda: 161.0)
    transport = Mock(return_value=SimpleNamespace())
    monkeypatch.setattr(chat, "streaming", transport)
    monkeypatch.setattr(
        chat,
        "get_turn_status",
        AsyncMock(return_value=SimpleNamespace(
            last_event_seq=0,
            terminal=None,
        )),
    )

    assert await event_stream._resume_stream(OSError("still offline")) is True
    assert event_stream._reconnect_started_at == 161.0
    assert event_stream._reconnect_failures == 1
    transport.assert_called_once()


@pytest.mark.anyio
async def test_valid_event_resets_consecutive_attach_budget(monkeypatch) -> None:
    event_stream = chat.stream_chat({}, "hello", [])
    event_stream._reconnect_failures = 4
    event_stream._reconnect_started_at = 100.0

    await event_stream._mark_transport_healthy()

    assert event_stream._reconnect_failures == 0
    assert event_stream._reconnect_started_at is None


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
            "type": "turn.completed",
            "turn_id": "turn_001",
            "event_seq": 2,
        }

    _install_reconnect_stream(monkeypatch, streaming)
    monkeypatch.setattr(chat, "STREAM_PAYLOAD_SILENCE_TIMEOUT_SEC", 0.01)
    event_stream = chat.stream_chat(
        {},
        "hello",
        [],
        timeout=1.0,
        on_recovery_status=_recovery_recorder(reconnecting),
    )

    events = [event async for event in event_stream]

    assert [type(event) for event in events] == [
        TextDeltaEvent,
        TurnCompletedEvent,
    ]
    assert calls == [
        "https://example.com/mind-chat",
        "https://example.com/mind-attach",
    ]
    assert reconnecting == [
        ("reconnecting", 1),
        ("replaying", 1),
        ("caught_up", 2),
    ]


@pytest.mark.anyio
async def test_replay_catches_up_before_waiting_for_the_next_live_event(monkeypatch) -> None:
    caught_up = asyncio.Event()
    release = asyncio.Event()

    async def recovery(phase, _seq):
        if phase == "caught_up":
            caught_up.set()

    async def streaming(url, _headers, _payload, _timeout):
        if url.endswith("/mind-chat"):
            yield {"type": "turn.started", "turn_id": "turn_001", "event_seq": 1}
            raise httpx.ReadError("connection closed")
        await release.wait()
        yield {"type": "turn.completed", "turn_id": "turn_001", "event_seq": 2}

    _install_reconnect_stream(monkeypatch, streaming, replay_target_seq=1)
    stream = chat.stream_chat({}, "hello", [], on_recovery_status=recovery)
    collecting = asyncio.create_task(_collect(stream))
    try:
        await asyncio.wait_for(caught_up.wait(), timeout=1.0)
        assert not collecting.done()
        release.set()
        events = await asyncio.wait_for(collecting, timeout=1.0)
        assert [event.type for event in events] == ["turn.started", "turn.completed"]
    finally:
        if not collecting.done():
            collecting.cancel()
        await asyncio.gather(collecting, return_exceptions=True)


@pytest.mark.anyio
async def test_missing_observed_turn_stops_recovery_without_resubmission(monkeypatch) -> None:
    calls = []

    async def streaming(url, _headers, _payload, _timeout):
        calls.append(url)
        yield {"type": "turn.started", "turn_id": "turn_001", "event_seq": 1}
        raise httpx.ReadError("connection closed")

    _install_reconnect_stream(monkeypatch, streaming)
    monkeypatch.setattr(chat, "get_turn_status", AsyncMock(side_effect=
        chat.TurnStatusRequestError("turn missing", status_code=404),
    ))
    stream = chat.stream_chat({}, "hello", [])
    with pytest.raises(chat.TurnStatusRequestError, match="no longer exists"):
        await asyncio.wait_for(_collect(stream), timeout=1.0)
    assert calls == ["https://example.com/mind-chat"]
    assert stream.end_reason == "fatal"


@pytest.mark.anyio
async def test_cursor_conflict_stops_attach_without_resetting_cursor(monkeypatch) -> None:
    async def streaming(url, _headers, _payload, _timeout):
        if url.endswith("/mind-chat"):
            raise httpx.ReadError("connection closed")
        request = httpx.Request("POST", url)
        response = httpx.Response(409, request=request, json={
            "details": {"code": "event_cursor_ahead", "last_event_seq": 21},
        })
        response.raise_for_status()
        yield {}

    _install_reconnect_stream(monkeypatch, streaming, replay_target_seq=21)
    stream = chat.stream_chat({}, "hello", [], initial_event_seq=236)
    with pytest.raises(httpx.HTTPStatusError):
        await asyncio.wait_for(_collect(stream), timeout=1.0)
    assert stream.last_event_seq == 236
    assert stream.end_reason == "fatal"


@pytest.mark.anyio
async def test_ping_cannot_mask_a_missing_terminal_event(monkeypatch) -> None:
    calls = []
    recovery = []

    async def streaming(url, _headers, _payload, _timeout):
        calls.append(url)
        while True:
            yield {"type": "ping"}
            await asyncio.sleep(0.001)

    terminal = SimpleNamespace(
        type="turn.completed",
        turn_id="turn_001",
        status="interrupted",
        error=None,
        last_event_seq=1,
        completed_at=10.0,
        duration_ms=1_000,
    )
    _install_reconnect_stream(monkeypatch, streaming)
    monkeypatch.setattr(chat, "STREAM_PAYLOAD_SILENCE_TIMEOUT_SEC", 0.01)
    monkeypatch.setattr(
        chat,
        "get_turn_status",
        AsyncMock(return_value=SimpleNamespace(
            last_event_seq=1,
            terminal=terminal,
        )),
    )

    event_stream = chat.stream_chat(
        {},
        "hello",
        [],
        timeout=1.0,
        on_recovery_status=_recovery_recorder(recovery),
    )
    events = await asyncio.wait_for(_collect(event_stream), timeout=1.0)

    assert len(events) == 1
    assert isinstance(events[0], TurnCompletedEvent)
    assert events[0].status == "interrupted"
    assert calls == ["https://example.com/mind-chat"]
    assert recovery == [
        ("reconnecting", 0),
        ("replaying", 0),
        ("caught_up", 1),
    ]


@pytest.mark.anyio
async def test_healthy_ping_keeps_long_tool_connection_open(monkeypatch) -> None:
    calls = []
    recovery = []
    queried = asyncio.Event()

    async def status(**_kwargs):
        queried.set()
        return SimpleNamespace(last_event_seq=0, terminal=None)

    async def streaming(url, _headers, _payload, _timeout):
        calls.append(url)
        while not queried.is_set():
            yield {"type": "ping"}
            await asyncio.sleep(0.001)
        yield {
            "type": "turn.completed", "turn_id": "turn_001", "event_seq": 1,
            "last_event_seq": 1, "status": "completed", "completed_at": 10.0,
        }

    _install_reconnect_stream(monkeypatch, streaming)
    monkeypatch.setattr(chat, "STREAM_PAYLOAD_SILENCE_TIMEOUT_SEC", 0.1)
    monkeypatch.setattr(chat, "get_turn_status", status)
    event_stream = chat.stream_chat(
        {}, "hello", [], timeout=1.0,
        on_recovery_status=_recovery_recorder(recovery),
    )
    events = await asyncio.wait_for(_collect(event_stream), timeout=1.0)
    assert queried.is_set()
    assert len(events) == 1
    assert calls == ["https://example.com/mind-chat"]
    assert recovery == []


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
                "phase": "commentary",
            }
            yield {
                "type": "text.delta",
                "turn_id": "turn_001",
                "event_seq": 2,
                "segment_id": "segment_1",
                "text": "second",
                "phase": "commentary",
            }
            raise OSError("connection lost")

        yield {
            "type": "text.delta",
            "turn_id": "turn_001",
            "event_seq": 2,
            "segment_id": "segment_1",
            "text": "duplicate",
            "phase": "commentary",
        }
        yield {
            "type": "text.delta",
            "turn_id": "turn_001",
            "event_seq": 3,
            "segment_id": "segment_1",
            "text": "third",
            "phase": "commentary",
        }
        yield {
            "type": "turn.completed",
            "turn_id": "turn_001",
            "event_seq": 4,
            "status": "completed",
        }

    _install_reconnect_stream(monkeypatch, streaming)

    event_stream = chat.stream_chat({}, "hello", [], timeout=12.0)
    events = [event async for event in event_stream]

    assert [event.event_seq for event in events] == [1, 2, 3, 4]
    assert [event.text for event in events if isinstance(event, TextDeltaEvent)] == [
        "first",
        "second",
        "third",
    ]
    assert [
        event.phase
        for event in events
        if isinstance(event, TextDeltaEvent)
    ] == ["commentary", "commentary", "commentary"]
    assert event_stream.last_event_seq == 4
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
async def test_replay_reports_caught_up_after_last_historical_event(
    monkeypatch,
) -> None:
    """验证恢复水位只在最后一条历史事件交付后切回实时模式。"""
    recovery = []

    async def streaming(url, _headers, _payload, _timeout):
        if url.endswith("/mind-chat"):
            yield {
                "type": "text.delta",
                "turn_id": "turn_001",
                "event_seq": 1,
                "segment_id": "segment_1",
                "text": "first",
            }
            raise OSError("connection lost")
        for event_seq in (2, 3, 4):
            yield {
                "type": "text.delta",
                "turn_id": "turn_001",
                "event_seq": event_seq,
                "segment_id": "segment_1",
                "text": str(event_seq),
            }
        yield {
            "type": "turn.completed",
            "turn_id": "turn_001",
            "event_seq": 5,
        }

    _install_reconnect_stream(
        monkeypatch,
        streaming,
        replay_target_seq=4,
    )
    event_stream = chat.stream_chat(
        {},
        "hello",
        [],
        on_recovery_status=_recovery_recorder(recovery),
    )
    iterator = event_stream.__aiter__()

    assert (await anext(iterator)).event_seq == 1
    assert (await anext(iterator)).event_seq == 2
    assert recovery == [
        ("reconnecting", 1),
        ("replaying", 1),
    ]
    assert (await anext(iterator)).event_seq == 3
    assert (await anext(iterator)).event_seq == 4
    assert recovery[-1] == ("replaying", 1)
    assert (await anext(iterator)).event_seq == 5
    assert recovery[-1] == ("caught_up", 4)


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
            "type": "turn.completed",
            "turn_id": "turn_001",
            "event_seq": 1,
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
    recovery = []
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
            "type": "turn.completed",
            "turn_id": "turn_001",
            "event_seq": 1,
        }

    _install_reconnect_stream(monkeypatch, streaming)
    monkeypatch.setattr(chat, "build_chat_payload", build_payload)
    monkeypatch.setattr(chat, "get_turn_status", status_probe)

    event_stream = chat.stream_chat(
        {},
        "hello",
        [],
        on_recovery_status=_recovery_recorder(recovery),
    )
    events = [event async for event in event_stream]

    assert len(events) == 1
    assert isinstance(events[0], TurnCompletedEvent)
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
    assert recovery == [
        ("reconnecting", 0),
        ("caught_up", 0),
    ]


@pytest.mark.anyio
@pytest.mark.parametrize("status_code", [None, 429, 503])
async def test_disconnect_before_first_event_attaches_when_status_unavailable(
    monkeypatch,
    status_code,
) -> None:
    calls = []
    status_probe = AsyncMock(side_effect=chat.TurnStatusRequestError(
        "turn status unavailable",
        status_code=status_code,
    ))

    async def streaming(url, _headers, payload, _timeout):
        calls.append((url, payload))
        if url.endswith("/mind-chat"):
            raise OSError("response headers were lost")
        yield {
            "type": "turn.completed",
            "turn_id": "turn_001",
            "event_seq": 1,
        }

    _install_reconnect_stream(monkeypatch, streaming)
    monkeypatch.setattr(chat, "get_turn_status", status_probe)

    event_stream = chat.stream_chat({}, "hello", [])
    events = [event async for event in event_stream]

    assert len(events) == 1
    assert isinstance(events[0], TurnCompletedEvent)
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
            "type": "turn.completed",
            "turn_id": "turn_001",
            "event_seq": 1,
        }

    _install_reconnect_stream(monkeypatch, streaming)
    monkeypatch.setattr(chat, "get_turn_status", status_probe)

    event_stream = chat.stream_chat({}, "hello", [])
    events = [event async for event in event_stream]

    assert len(events) == 1
    assert isinstance(events[0], TurnCompletedEvent)
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
    status_probe = AsyncMock(return_value=SimpleNamespace(
        last_event_seq=3,
        terminal=None,
    ))

    async def streaming(url, _headers, payload, _timeout):
        calls.append((url, payload))
        if url.endswith("/mind-chat"):
            raise OSError("response headers were lost")
        yield {
            "type": "turn.completed",
            "turn_id": "turn_001",
            "event_seq": 3,
        }

    _install_reconnect_stream(monkeypatch, streaming)
    monkeypatch.setattr(chat, "get_turn_status", status_probe)

    event_stream = chat.stream_chat({}, "hello", [])
    events = [event async for event in event_stream]

    assert len(events) == 1
    assert isinstance(events[0], TurnCompletedEvent)
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
            "type": "turn.completed",
            "turn_id": "turn_001",
            "event_seq": 2,
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
async def test_attach_retries_beyond_previous_budget_until_completed(
    monkeypatch,
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
                "text": "partial",
            }
            raise OSError("connection lost")

        if len(calls) < 7:
            raise OSError("connection lost")
        yield {
            "type": "turn.completed",
            "turn_id": "turn_001",
            "event_seq": 2,
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
            "type": "turn.completed",
            "turn_id": "turn_001",
            "event_seq": 2,
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
async def test_completed_event_alone_closes_recovery_stream(monkeypatch) -> None:
    async def payloads():
        yield {
            "type": "turn.completed",
            "turn_id": "turn_001",
            "event_seq": 7,
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
    assert isinstance(events[0], TurnCompletedEvent)
    assert event_stream.end_reason == "settled"
    assert payload_stream.closed is True


@pytest.mark.anyio
async def test_filtered_session_sequence_advances_without_false_gap(monkeypatch) -> None:
    calls = []

    async def streaming(url, _headers, _payload, _timeout):
        calls.append(url)
        yield {
            "type": "text.delta",
            "turn_id": "turn_001",
            "event_seq": 1,
            "segment_id": "segment_1",
            "text": "one",
        }
        # event_seq=2 belongs to a filtered Session-level queue.changed event.
        yield {
            "type": "text.delta",
            "turn_id": "turn_001",
            "event_seq": 3,
            "segment_id": "segment_1",
            "text": "three",
        }
        yield {
            "type": "turn.completed",
            "turn_id": "turn_001",
            "event_seq": 4,
        }

    _install_reconnect_stream(monkeypatch, streaming)

    events = [event async for event in chat.stream_chat({}, "hello", [])]

    assert [event.event_seq for event in events] == [1, 3, 4]
    assert [
        event.text for event in events if isinstance(event, TextDeltaEvent)
    ] == ["one", "three"]
    assert calls == ["https://example.com/mind-chat"]


@pytest.mark.anyio
async def test_attach_restores_approval_snapshot_before_replay(monkeypatch) -> None:
    calls = []
    restored = []
    snapshot = ToolApprovalSnapshot(
        cid="cid_1",
        sid="sid_1",
        turn_id="turn_001",
        turn_status="waiting_approval",
        terminal=None,
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
            "type": "turn.completed",
            "turn_id": "turn_001",
            "event_seq": 2,
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
async def test_snapshot_transient_failure_retries_before_event_attach(
    monkeypatch,
) -> None:
    calls = []
    restored = []
    snapshot = ToolApprovalSnapshot(
        cid="cid_1",
        sid="sid_1",
        turn_id="turn_001",
        turn_status="waiting_approval",
        terminal=None,
        last_event_seq=1,
        approvals=(),
    )

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
            "type": "turn.completed",
            "turn_id": "turn_001",
            "event_seq": 2,
        }

    _install_reconnect_stream(monkeypatch, streaming)
    monkeypatch.setattr(
        chat,
        "reconcile_tool_approval_snapshot",
        AsyncMock(side_effect=(
            chat.ToolApprovalSnapshotRequestError(
                "snapshot unavailable",
                status_code=503,
                retryable=True,
            ),
            snapshot,
        )),
    )
    wait_before_attach = AsyncMock()
    monkeypatch.setattr(chat.TurnEventStream, "_wait_before_attach", wait_before_attach)

    async def restore(value):
        restored.append(value)

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
    assert chat.reconcile_tool_approval_snapshot.await_count == 2
    assert chat.get_turn_status.await_count == 2
    assert calls == [
        "https://example.com/mind-chat",
        "https://example.com/mind-attach",
    ]


@pytest.mark.anyio
async def test_snapshot_contract_failure_prevents_event_attach(monkeypatch) -> None:
    calls = []

    async def streaming(url, _headers, _payload, _timeout):
        calls.append(url)
        yield {
            "type": "text.delta",
            "turn_id": "turn_001",
            "event_seq": 1,
            "segment_id": "segment_1",
            "text": "first",
        }
        raise OSError("connection lost")

    _install_reconnect_stream(monkeypatch, streaming)
    monkeypatch.setattr(
        chat,
        "reconcile_tool_approval_snapshot",
        AsyncMock(side_effect=chat.ToolApprovalSnapshotRequestError(
            "snapshot contract is invalid",
            status_code=200,
            retryable=False,
        )),
    )
    monkeypatch.setattr(
        chat.TurnEventStream,
        "_wait_before_attach",
        AsyncMock(),
    )
    stream = chat.stream_chat(
        {},
        "hello",
        [],
        on_approval_snapshot=AsyncMock(),
    )

    with pytest.raises(
        chat.ToolApprovalSnapshotRequestError,
        match="contract is invalid",
    ):
        _ = [event async for event in stream]

    assert stream.end_reason == "fatal"
    assert calls == ["https://example.com/mind-chat"]


@pytest.mark.anyio
async def test_control_probe_interrupts_snapshot_retry_backoff(monkeypatch) -> None:
    calls = []
    authority_retry_started = asyncio.Event()
    waits = []

    async def streaming(url, _headers, _payload, _timeout):
        calls.append(url)
        yield {
            "type": "text.delta",
            "turn_id": "turn_001",
            "event_seq": 1,
            "segment_id": "segment_1",
            "text": "first",
        }
        raise OSError("connection lost")

    async def wait_before_attach(_delay):
        waits.append(_delay)
        if len(waits) == 1:
            return
        authority_retry_started.set()
        await asyncio.Future()

    active_status = SimpleNamespace(last_event_seq=1, terminal=None)
    terminal_status = SimpleNamespace(
        last_event_seq=2,
        terminal=SimpleNamespace(
            type="turn.completed",
            turn_id="turn_001",
            status="interrupted",
            error=None,
            last_event_seq=2,
            completed_at=12.5,
            duration_ms=2_500,
        ),
    )
    _install_reconnect_stream(monkeypatch, streaming)
    monkeypatch.setattr(
        chat,
        "get_turn_status",
        AsyncMock(side_effect=(active_status, terminal_status)),
    )
    monkeypatch.setattr(
        chat,
        "reconcile_tool_approval_snapshot",
        AsyncMock(side_effect=chat.ToolApprovalSnapshotRequestError(
            "snapshot unavailable",
            status_code=503,
            retryable=True,
        )),
    )
    monkeypatch.setattr(
        chat.TurnEventStream,
        "_wait_before_attach",
        staticmethod(wait_before_attach),
    )
    stream = chat.stream_chat(
        {},
        "hello",
        [],
        on_approval_snapshot=AsyncMock(),
    )
    events = stream.__aiter__()

    first = await anext(events)
    terminal_task = asyncio.create_task(anext(events))
    await authority_retry_started.wait()
    stream.request_recovery_probe()
    terminal = await asyncio.wait_for(terminal_task, timeout=1.0)

    assert isinstance(first, TextDeltaEvent)
    assert isinstance(terminal, TurnCompletedEvent)
    assert terminal.status == "interrupted"
    assert chat.get_turn_status.await_count == 2
    assert chat.reconcile_tool_approval_snapshot.await_count == 1
    assert calls == ["https://example.com/mind-chat"]


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
