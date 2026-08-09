# -*- coding: utf-8 -*-

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from mind_app.subscription.forwarding import (
    AgentExecutor,
    AgentInbox,
    InboxForwardHandler,
    normalize_forward_request,
)
from mind_app.subscription.models import AgentForwardRequest
from mind_app.subscription.models import AgentLiveStatus
from mind_app.subscription.ws import handle_server_message, recv_json_or_stop
from mind_app.runtime.turns.result import RunResult


def _forward_message() -> dict[str, object]:
    return {
        "type": "mind.forward",
        "seq": 7,
        "session_id": "agent-session",
        "message_id": "message-1",
        "cid": "cid-1",
        "sid": "sid-1",
        "payload": {
            "call_id": "call-1",
            "message": "inspect workspace",
        },
    }


def _recording_mock(events: list[str], name: str, result=None) -> AsyncMock:
    def record(*_args, **_kwargs):
        events.append(name)
        return result

    return AsyncMock(side_effect=record)


def test_normalize_forward_request_requires_message() -> None:
    with pytest.raises(ValueError, match="payload.message must be a string"):
        normalize_forward_request({})

    with pytest.raises(ValueError, match="payload.message must be non-empty"):
        normalize_forward_request({"message": "  "})


def test_normalize_forward_request_preserves_message_and_intent() -> None:
    assert normalize_forward_request({
        "message": " inspect ",
        "intent": {"summary": "diagnose"},
    }) == (" inspect ", "diagnose")


@pytest.mark.anyio
async def test_agent_executor_runs_message_and_sends_completion() -> None:
    result = RunResult(status="completed", assistant_text="done")
    mind = SimpleNamespace(calling=AsyncMock(return_value=result))
    client = SimpleNamespace(
        send_mind_started=AsyncMock(),
        send_mind_completed=AsyncMock(),
    )
    runtime = SimpleNamespace(session_id="agent-session")
    request = AgentForwardRequest(
        message_id="message-1",
        call_id="call-1",
        cid="cid-1",
        sid="sid-1",
        payload={
            "message": "inspect workspace",
            "metadata": {"origin": "server"},
            "intent": {"summary": "inspect"},
        },
    )

    await AgentExecutor().execute(
        mind,
        client,
        object(),
        runtime,
        request,
    )

    mind.calling.assert_awaited_once_with(
        message="inspect workspace",
        metadata={
            "origin": "server",
            "cid": "cid-1",
            "sid": "sid-1",
            "intent_summary": "inspect",
        },
    )
    client.send_mind_started.assert_awaited_once()
    client.send_mind_completed.assert_awaited_once()


@pytest.mark.anyio
async def test_agent_ws_enqueues_message_without_executing_it() -> None:
    events: list[str] = []
    mind = SimpleNamespace(
        calling=_recording_mock(
            events,
            "calling",
            RunResult(status="completed", assistant_text="done"),
        ),
    )
    client = SimpleNamespace(
        send_mind_received=_recording_mock(events, "received"),
    )
    runtime = SimpleNamespace(
        session_id="agent-session",
        forwarded_message_ids=None,
    )
    inbox = AgentInbox()

    seq = await handle_server_message(
        mind,
        client,
        object(),
        runtime,
        _forward_message(),
        AgentLiveStatus(),
        InboxForwardHandler(inbox),
    )

    assert seq == 7
    mind.calling.assert_not_awaited()
    assert events == ["received"]
    assert [item.request.message_id for item in inbox.pending_items()] == [
        "message-1"
    ]


@pytest.mark.anyio
async def test_agent_ws_replay_acknowledges_without_duplicate_inbox_item() -> None:
    mind = SimpleNamespace(
        calling=AsyncMock(
            return_value=RunResult(status="completed", assistant_text="done")
        ),
    )
    client = SimpleNamespace(
        send_mind_received=AsyncMock(),
    )
    runtime = SimpleNamespace(
        session_id="agent-session",
        forwarded_message_ids=None,
    )
    connection = object()
    live_status = AgentLiveStatus()
    message = _forward_message()
    inbox = AgentInbox()
    handler = InboxForwardHandler(inbox)

    await handle_server_message(
        mind, client, connection, runtime, message, live_status, handler
    )
    await handle_server_message(
        mind, client, connection, runtime, message, live_status, handler
    )

    assert client.send_mind_received.await_count == 2
    mind.calling.assert_not_awaited()
    assert len(inbox.items) == 1


@pytest.mark.anyio
async def test_agent_ws_adds_message_before_acknowledging_it() -> None:
    events: list[str] = []

    class RecordingInbox(AgentInbox):
        def add(self, request: AgentForwardRequest):
            events.append("added")
            return super().add(request)

    client = SimpleNamespace(
        send_mind_received=_recording_mock(events, "received"),
    )
    runtime = SimpleNamespace(
        session_id="agent-session",
        forwarded_message_ids=None,
    )

    await handle_server_message(
        SimpleNamespace(),
        client,
        object(),
        runtime,
        _forward_message(),
        AgentLiveStatus(),
        InboxForwardHandler(RecordingInbox()),
    )

    assert events == ["added", "received"]


@pytest.mark.anyio
async def test_agent_heartbeat_does_not_change_visible_listener_status() -> None:
    client = SimpleNamespace(send_pong=AsyncMock())
    connection = object()
    runtime = SimpleNamespace(session_id="agent-session")
    status = AgentLiveStatus(
        title="Subscription Online",
        detail="Waiting for Server Tasks",
    )

    seq = await handle_server_message(
        SimpleNamespace(),
        client,
        connection,
        runtime,
        {"type": "ping", "seq": 8},
        status,
    )

    assert seq == 8
    assert status.snapshot() == (
        "Subscription Online",
        "Waiting for Server Tasks",
    )
    client.send_pong.assert_awaited_once_with(
        connection,
        session_id="agent-session",
    )


@pytest.mark.anyio
async def test_agent_receive_stops_cleanly_when_listener_is_closed() -> None:
    stop_event = asyncio.Event()
    stop_event.set()
    client = SimpleNamespace(recv_json=AsyncMock())

    with pytest.raises(asyncio.CancelledError):
        await recv_json_or_stop(client, object(), stop_event)
