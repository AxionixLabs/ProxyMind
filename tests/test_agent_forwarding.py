# -*- coding: utf-8 -*-

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from mind_app.modes.agent.forwarding import (
    AgentExecutor,
    normalize_forward_target,
)
from mind_app.modes.agent.models import AgentForwardRequest
from mind_app.modes.agent.models import AgentLiveStatus
from mind_app.modes.agent.ws import handle_server_message
from mind_app.modes.result import RunResult


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
            "mode": "fast",
            "message": "inspect workspace",
        },
    }


def _recording_mock(events: list[str], name: str, result=None) -> AsyncMock:
    def record(*_args, **_kwargs):
        events.append(name)
        return result

    return AsyncMock(side_effect=record)


def test_normalize_forward_target_requires_message() -> None:
    with pytest.raises(ValueError, match="payload.message must be a string"):
        normalize_forward_target({"mode": "chat"})

    with pytest.raises(ValueError, match="payload.message must be non-empty"):
        normalize_forward_target({"mode": "chat", "message": "  "})


def test_normalize_forward_target_preserves_message_and_intent() -> None:
    assert normalize_forward_target({
        "mode": "XTRA",
        "message": " inspect ",
        "intent": {"summary": "diagnose"},
    }) == ("xtra", " inspect ", "diagnose")


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
            "mode": "fast",
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
        mode="fast",
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
async def test_agent_executor_reports_invalid_message_failure() -> None:
    task_event = asyncio.Event()
    task_event.set()
    mind = SimpleNamespace(task_event=task_event, calling=AsyncMock())
    client = SimpleNamespace(send_mind_failed=AsyncMock())
    runtime = SimpleNamespace(
        session_id="agent-session",
        pending_tasks=set(),
    )
    connection = object()
    request = AgentForwardRequest(
        message_id="message-1",
        call_id="call-1",
        cid="cid-1",
        sid="sid-1",
        payload={"mode": "chat"},
    )

    AgentExecutor().spawn(
        mind,
        client,
        connection,
        runtime,
        request,
    )
    tasks = tuple(runtime.pending_tasks)
    await asyncio.gather(*tasks)

    mind.calling.assert_not_awaited()
    client.send_mind_failed.assert_awaited_once_with(
        connection,
        session_id="agent-session",
        cid="cid-1",
        sid="sid-1",
        call_id="call-1",
        error_type="ValueError",
        error_message="mind.forward payload.message must be a string",
    )


@pytest.mark.anyio
async def test_agent_ws_executes_message_without_blocking_handler() -> None:
    events: list[str] = []
    task_event = asyncio.Event()
    task_event.set()
    mind = SimpleNamespace(
        await_cleanup=AsyncMock(),
        stop_anim=Mock(return_value=None),
        task_event=task_event,
        calling=_recording_mock(
            events,
            "calling",
            RunResult(status="completed", assistant_text="done"),
        ),
    )
    client = SimpleNamespace(
        send_mind_received=_recording_mock(events, "received"),
        send_mind_started=_recording_mock(events, "started"),
        send_mind_completed=_recording_mock(events, "completed"),
        send_mind_failed=AsyncMock(),
    )
    runtime = SimpleNamespace(
        session_id="agent-session",
        forwarded_message_ids=None,
        pending_tasks=None,
    )

    seq = await handle_server_message(
        mind,
        client,
        object(),
        runtime,
        _forward_message(),
        AgentLiveStatus(),
    )

    assert seq == 7
    mind.calling.assert_not_awaited()
    assert events == ["received"]

    tasks = tuple(runtime.pending_tasks)
    await asyncio.gather(*tasks)

    assert events == ["received", "started", "calling", "completed"]
    client.send_mind_failed.assert_not_awaited()


@pytest.mark.anyio
async def test_agent_ws_replay_acknowledges_without_reexecuting() -> None:
    task_event = asyncio.Event()
    task_event.set()
    mind = SimpleNamespace(
        await_cleanup=AsyncMock(),
        stop_anim=Mock(return_value=None),
        task_event=task_event,
        calling=AsyncMock(
            return_value=RunResult(status="completed", assistant_text="done")
        ),
    )
    client = SimpleNamespace(
        send_mind_received=AsyncMock(),
        send_mind_started=AsyncMock(),
        send_mind_completed=AsyncMock(),
        send_mind_failed=AsyncMock(),
    )
    runtime = SimpleNamespace(
        session_id="agent-session",
        forwarded_message_ids=None,
        pending_tasks=None,
    )
    connection = object()
    live_status = AgentLiveStatus()
    message = _forward_message()

    await handle_server_message(
        mind, client, connection, runtime, message, live_status
    )
    await asyncio.gather(*tuple(runtime.pending_tasks))
    await handle_server_message(
        mind, client, connection, runtime, message, live_status
    )

    assert client.send_mind_received.await_count == 2
    mind.calling.assert_awaited_once()
    client.send_mind_started.assert_awaited_once()
    client.send_mind_completed.assert_awaited_once()
    client.send_mind_failed.assert_not_awaited()
