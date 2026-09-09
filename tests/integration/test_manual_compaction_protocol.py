# -*- coding: utf-8 -*-

import contextlib
import json
import typing
from dataclasses import dataclass
from unittest.mock import patch

import httpx
import pytest

from agent.adapters.protocol.compaction import ProtocolCompactionClient
from protocol.client import compact
from protocol.schema.json_value import (
    JsonObject,
    JsonValue,
)
from protocol.schema.stream_events import (
    parse_compact_event,
    parse_stream_event,
)


def _event(status: str, **fields: JsonValue) -> JsonObject:
    payload: JsonObject = {
        "proto": "mind.chat",
        "type": f"context.compaction.{status}",
        "cid": "cid-1",
        "sid": "sid-1",
        "turn_id": "compact-turn-1",
        "item_id": "compaction-1",
        "item_kind": "context_compaction",
        "item_status": "in_progress" if status == "started" else status,
        "phase": "standalone",
        "trigger": "manual",
        "reason": "user_requested",
        "presentation_epoch": 1,
    }
    if status == "failed":
        payload.update(
            reason="operation_failed",
            error_type="operation_failed",
            retryable=True,
        )
    payload.update(fields)
    return payload


class _SseBody(httpx.AsyncByteStream):
    def __init__(self, content: str) -> None:
        self.content = content.encode("utf-8")

    async def __aiter__(self) -> typing.AsyncIterator[bytes]:
        for offset in range(0, len(self.content), 7):
            yield self.content[offset:offset + 7]


@dataclass
class _CompactExchange:
    requests: list[httpx.Request]
    response: httpx.Response
    client: httpx.AsyncClient


@contextlib.contextmanager
def _compact_http(
    events: list[JsonValue],
    *,
    raw_body: str | None = None,
    status_code: int = 200,
    error: httpx.RequestError | None = None,
) -> typing.Iterator[_CompactExchange]:
    requests: list[httpx.Request] = []
    body = raw_body if raw_body is not None else ": heartbeat\n\n" + "".join(
        f"data: {json.dumps(event)}\n\n" for event in events
    )
    response = httpx.Response(
        status_code,
        headers={"content-type": "text/event-stream"},
        stream=_SseBody(body),
    )

    def handle(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if error is not None:
            raise error
        return response

    client = httpx.AsyncClient(transport=httpx.MockTransport(handle))
    with (
        patch("protocol.transport.streaming.httpx.AsyncClient", return_value=client),
        patch.object(compact, "build_service_headers", return_value={}),
        patch.object(
            compact.service_endpoints,
            "endpoint",
            return_value="https://appserver.test/compact",
        ),
    ):
        yield _CompactExchange(requests, response, client)


async def _collect():
    return [
        event async for event in ProtocolCompactionClient().stream(
            cid="cid-1", sid="sid-1", pref_config={},
        )
    ]


@pytest.mark.anyio
async def test_manual_compaction_consumes_current_server_sse_and_closes_at_terminal() -> None:
    with _compact_http([
        _event("started", event_seq=10),
        _event("completed", event_seq=11, before_items=18, after_items=6),
        {"type": "must-not-consume-after-terminal"},
    ]) as exchange:
        async with contextlib.aclosing(ProtocolCompactionClient().stream(
            cid="cid-1", sid="sid-1", pref_config={},
        )) as stream:
            started = await anext(stream)
            assert not exchange.response.is_closed
            completed = await anext(stream)
            assert exchange.response.is_closed
            assert exchange.client.is_closed
            events = [started, completed]

    assert [event.status for event in events] == ["started", "completed"]
    assert events[-1].message == "Context compacted."
    assert events[-1].before_items == 18
    assert events[-1].after_items == 6
    assert events[-1].summary == ""
    assert len(exchange.requests) == 1
    request = exchange.requests[0]
    assert request.method == "POST"
    assert request.url.path == "/compact"
    payload = json.loads(request.content)
    assert payload["cid"] == "cid-1"
    assert payload["sid"] == "sid-1"
    assert payload["strategy"] == "memento"
    assert exchange.response.is_closed
    assert exchange.client.is_closed


@pytest.mark.anyio
@pytest.mark.parametrize(("error_type", "message"), [
    ("operation_failed", "Context compaction failed. Please try again."),
    ("summary_failed", "Context compaction failed. Please try again."),
    ("persist_failed", "Failed to save the compacted context. Please try again."),
    ("cas_conflict", "Conversation changed while compacting. Please try again."),
])
async def test_manual_compaction_maps_server_failure_without_event_sequence(
    error_type: str,
    message: str,
) -> None:
    with _compact_http([_event("failed", error_type=error_type)]) as exchange:
        async with contextlib.aclosing(ProtocolCompactionClient().stream(
            cid="cid-1", sid="sid-1", pref_config={},
        )) as stream:
            events = [await anext(stream)]
            assert exchange.response.is_closed
            assert exchange.client.is_closed

    assert [event.status for event in events] == ["failed"]
    assert events[0].message == message
    assert len(exchange.requests) == 1
    assert exchange.response.is_closed
    assert exchange.client.is_closed


def test_manual_failure_does_not_weaken_chat_event_sequence_requirement() -> None:
    payload = _event("failed")

    assert parse_compact_event(payload).event_seq is None
    with pytest.raises(ValueError, match="event_seq"):
        parse_stream_event(payload)


@pytest.mark.anyio
@pytest.mark.parametrize("wire_events", [[], [_event("started", event_seq=1)]])
async def test_manual_compaction_incomplete_stream_fails_without_resubmission(wire_events) -> None:
    with _compact_http(wire_events) as exchange:
        events = await _collect()

    assert events[-1].status == "failed"
    assert all(event.status != "completed" for event in events)
    assert len(exchange.requests) == 1
    assert exchange.response.is_closed
    assert exchange.client.is_closed


@pytest.mark.anyio
@pytest.mark.parametrize(("status_code", "message"), [
    (404, "There is no conversation history to compact."),
    (409, "Conversation is busy or changed. Try /compact again after the current operation finishes."),
    (502, "Context compaction failed. Please try again."),
])
async def test_manual_compaction_http_failure_is_local_and_not_retried(
    status_code: int,
    message: str,
) -> None:
    with _compact_http([], status_code=status_code) as exchange:
        events = await _collect()

    assert [event.status for event in events] == ["failed"]
    assert events[0].message == message
    assert len(exchange.requests) == 1
    assert exchange.response.is_closed
    assert exchange.client.is_closed


@pytest.mark.anyio
async def test_manual_compaction_timeout_fails_without_resubmission() -> None:
    with _compact_http([], error=httpx.ReadTimeout("timeout")) as exchange:
        events = await _collect()

    assert [event.status for event in events] == ["failed"]
    assert len(exchange.requests) == 1
    assert exchange.client.is_closed


@pytest.mark.anyio
async def test_manual_compaction_corrupt_json_is_not_skipped_before_success() -> None:
    with _compact_http([], raw_body=(
        "data: {invalid\n\n"
        f"data: {json.dumps(_event('completed'))}\n\n"
    )) as exchange:
        events = await _collect()

    assert [event.status for event in events] == ["failed"]
    assert exchange.response.is_closed
    assert exchange.client.is_closed


@pytest.mark.anyio
@pytest.mark.parametrize("payload", [
    [],
    {"type": "conversation.compact", "summary": "old wire format"},
    _event("completed", cid="another-conversation"),
    _event("completed", sid="another-session"),
    _event("completed", item_status="in_progress"),
    _event("completed", before_items=-1),
    _event("completed", phase="mid_turn", trigger="automatic"),
    _event("completed", summary="removed field"),
    _event("failed", retryable="true"),
])
async def test_manual_compaction_rejects_invalid_or_unrelated_events(payload) -> None:
    with _compact_http([payload]) as exchange:
        with pytest.raises(ValueError):
            await _collect()

    assert exchange.response.is_closed
    assert exchange.client.is_closed


@pytest.mark.anyio
@pytest.mark.parametrize("field", ["turn_id", "item_id"])
async def test_manual_compaction_rejects_switching_operation_identity(field: str) -> None:
    with _compact_http([
        _event("started"),
        _event("completed", **{field: "another-operation"}),
    ]) as exchange:
        with pytest.raises(ValueError, match="current operation"):
            await _collect()

    assert exchange.response.is_closed
    assert exchange.client.is_closed
