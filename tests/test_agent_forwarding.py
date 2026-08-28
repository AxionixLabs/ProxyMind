# -*- coding: utf-8 -*-

import asyncio
from types import SimpleNamespace
from unittest.mock import ANY, AsyncMock, Mock

import pytest

from mind_app.subscription.forwarding import (
    AgentExecutor,
    AgentInbox,
    InboxForwardHandler,
    normalize_forward_request,
)
from mind_app.subscription.models import (
    AgentForwardRequest,
    AgentLiveStatus,
    AgentSessionRuntime,
)
from mind_app.subscription.status import AgentStatusOutbox
from mind_app.subscription.ws import (
    build_runtime_llm_conf,
    connect_once,
    handle_server_message,
    recv_json_or_stop,
)
from mind_app.runtime.agent.client import AgentClient
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


@pytest.mark.anyio
async def test_runtime_bind_maps_provider_kind_to_remote_provider() -> None:
    mind = SimpleNamespace(fresh_pref_config=AsyncMock(return_value={
        "primary": {
            "provider": "claude-main",
            "kind": "anthropic",
            "route": "messages",
            "model": "claude-test",
            "enabled": True,
        },
    }))

    config = await build_runtime_llm_conf(mind)

    assert config["primary"] == {
        "provider": "anthropic",
        "route": "messages",
        "model": "claude-test",
    }


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
    turn_runner = AsyncMock(return_value=result)
    mind = SimpleNamespace()
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

    await AgentExecutor(turn_runner).execute(
        mind,
        client,
        object(),
        runtime,
        request,
    )

    turn_runner.assert_awaited_once_with(
        mind,
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
async def test_agent_executor_propagates_tui_turn_id() -> None:
    result = RunResult(status="completed", assistant_text="done")
    turn_runner = AsyncMock(return_value=result)
    mind = SimpleNamespace()
    client = SimpleNamespace(
        send_mind_started=AsyncMock(),
        send_mind_completed=AsyncMock(),
    )
    request = AgentForwardRequest(
        message_id="message-1",
        call_id="call-1",
        cid="cid-1",
        sid="sid-1",
        payload={"message": "inspect workspace"},
    )

    await AgentExecutor(turn_runner).execute(
        mind,
        client,
        object(),
        SimpleNamespace(session_id="agent-session"),
        request,
        turn_id="turn_remote",
    )

    assert turn_runner.await_args.kwargs["turn_id"] == "turn_remote"


@pytest.mark.anyio
async def test_agent_executor_reports_interrupted_result_as_cancelled() -> None:
    turn_runner = AsyncMock(return_value=RunResult(status="interrupted"))
    mind = SimpleNamespace()
    client = SimpleNamespace(
        send_mind_started=AsyncMock(),
        send_mind_cancelled=AsyncMock(),
    )
    request = AgentForwardRequest(
        message_id="message-1",
        call_id="call-1",
        cid="cid-1",
        sid="sid-1",
        payload={"message": "inspect workspace"},
    )

    await AgentExecutor(turn_runner).execute(
        mind,
        client,
        object(),
        SimpleNamespace(session_id="agent-session"),
        request,
    )

    client.send_mind_cancelled.assert_awaited_once_with(
        ANY,
        session_id="agent-session",
        cid="cid-1",
        sid="sid-1",
        call_id="call-1",
        reason="user_interrupted",
    )


@pytest.mark.anyio
async def test_agent_executor_reports_task_cancellation_as_cancelled() -> None:
    started = asyncio.Event()

    async def run_root_turn(_controller, **_kwargs):
        started.set()
        await asyncio.Future()

    mind = SimpleNamespace()
    client = SimpleNamespace(
        send_mind_started=AsyncMock(),
        send_mind_cancelled=AsyncMock(),
    )
    request = AgentForwardRequest(
        message_id="message-1",
        call_id="call-1",
        cid="cid-1",
        sid="sid-1",
        payload={"message": "inspect workspace"},
    )

    task = asyncio.create_task(AgentExecutor(run_root_turn).execute(
        mind,
        client,
        object(),
        SimpleNamespace(session_id="agent-session"),
        request,
    ))
    await started.wait()
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task

    client.send_mind_cancelled.assert_awaited_once()
    assert client.send_mind_cancelled.await_args.kwargs["reason"] == "user_interrupted"


@pytest.mark.anyio
async def test_agent_executor_reports_execution_failure() -> None:
    error = RuntimeError("execution failed")
    turn_runner = AsyncMock(side_effect=error)
    mind = SimpleNamespace()
    client = SimpleNamespace(
        send_mind_started=AsyncMock(),
        send_mind_failed=AsyncMock(),
    )
    request = AgentForwardRequest(
        message_id="message-1",
        call_id="call-1",
        cid="cid-1",
        sid="sid-1",
        payload={"message": "inspect workspace"},
    )

    with pytest.raises(RuntimeError, match="execution failed"):
        await AgentExecutor(turn_runner).execute(
            mind,
            client,
            object(),
            SimpleNamespace(session_id="agent-session"),
            request,
        )

    client.send_mind_failed.assert_awaited_once_with(
        ANY,
        session_id="agent-session",
        cid="cid-1",
        sid="sid-1",
        call_id="call-1",
        error_type="RuntimeError",
        error_message="execution failed",
    )


@pytest.mark.anyio
async def test_terminal_outbox_resends_stable_envelope_until_ack() -> None:
    outbox = AgentStatusOutbox()
    client = SimpleNamespace(send_json=AsyncMock())
    runtime = _ws_runtime()
    request = AgentForwardRequest(
        message_id="message-1",
        call_id="call-1",
        cid="cid-1",
        sid="sid-1",
        payload={"message": "inspect workspace"},
    )
    first_connection = object()
    second_connection = object()

    outbox.bind(client, first_connection, runtime)
    await outbox.cancelled(
        request,
        session_id=runtime.session_id,
        reason="message_deleted",
    )
    first_envelope = client.send_json.await_args.args[1]

    outbox.unbind()
    outbox.bind(client, second_connection, runtime)
    await outbox.flush()
    resent_envelope = client.send_json.await_args.args[1]

    assert resent_envelope == first_envelope
    assert first_envelope["type"] == "mind.cancelled"
    assert first_envelope["payload"]["reason"] == "message_deleted"

    await handle_server_message(
        SimpleNamespace(),
        client,
        second_connection,
        runtime,
        {
            "type": "ack",
            "payload": {"acked_message_id": first_envelope["message_id"]},
        },
        AgentLiveStatus(),
        on_ack=outbox.acknowledge,
    )

    assert outbox.pending == {}


@pytest.mark.anyio
async def test_agent_ws_enqueues_message_without_executing_it() -> None:
    events: list[str] = []
    mind = SimpleNamespace()
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
    assert events == ["received"]
    assert client.send_mind_received.await_args.kwargs["disposition"] == "queued"
    assert [item.request.message_id for item in inbox.pending_items()] == [
        "message-1"
    ]


@pytest.mark.anyio
async def test_agent_ws_replay_acknowledges_without_duplicate_inbox_item() -> None:
    mind = SimpleNamespace()
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
    assert len(inbox.items) == 1


@pytest.mark.anyio
async def test_agent_ws_marks_auto_run_receipt_intent() -> None:
    client = SimpleNamespace(send_mind_received=AsyncMock())
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
        InboxForwardHandler(
            AgentInbox(),
            disposition_resolver=lambda: "auto_run",
        ),
    )

    assert client.send_mind_received.await_args.kwargs["disposition"] == "auto_run"


@pytest.mark.anyio
async def test_agent_client_serializes_receipt_disposition() -> None:
    client = AgentClient(base_url="https://example.test")
    client.send_json = AsyncMock()
    connection = object()

    await client.send_mind_received(
        connection,
        session_id="agent-session",
        cid="cid-1",
        sid="sid-1",
        call_id="call-1",
        acked_message_id="message-1",
        disposition="auto_run",
    )

    envelope = client.send_json.await_args.args[1]
    assert envelope["type"] == "mind.received"
    assert envelope["payload"] == {
        "call_id": "call-1",
        "acked_message_id": "message-1",
        "disposition": "auto_run",
    }


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
        InboxForwardHandler(
            RecordingInbox(),
            changed_callback=lambda: events.append("changed"),
        ),
    )

    assert events == ["added", "received", "changed"]


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
async def test_agent_ready_notifies_listener_readiness() -> None:
    ready = Mock()
    runtime = SimpleNamespace(
        session_id="agent-session",
        ready_received=False,
        pre_ready_connect_failures=3,
    )
    status = AgentLiveStatus()

    seq = await handle_server_message(
        SimpleNamespace(),
        SimpleNamespace(),
        object(),
        runtime,
        {"type": "ready", "seq": 9, "payload": {}},
        status,
        on_ready=ready,
    )

    assert seq == 9
    assert runtime.ready_received
    assert runtime.pre_ready_connect_failures == 0
    assert status.snapshot() == (
        "Subscription Online",
        "Handshake complete, waiting for tasks",
    )
    ready.assert_called_once_with()


@pytest.mark.anyio
async def test_agent_non_ready_messages_do_not_notify_readiness() -> None:
    ready = Mock()
    client = SimpleNamespace(send_pong=AsyncMock())
    runtime = SimpleNamespace(session_id="agent-session")

    await handle_server_message(
        SimpleNamespace(),
        client,
        object(),
        runtime,
        {"type": "ping", "seq": 10},
        AgentLiveStatus(),
        on_ready=ready,
    )

    ready.assert_not_called()


@pytest.mark.anyio
async def test_agent_replay_propagates_ready_notification() -> None:
    ready = Mock()
    runtime = SimpleNamespace(
        session_id="agent-session",
        ready_received=False,
        pre_ready_connect_failures=1,
    )

    seq = await handle_server_message(
        SimpleNamespace(),
        SimpleNamespace(),
        object(),
        runtime,
        {
            "type": "replay.batch",
            "seq": 10,
            "payload": {
                "messages": [
                    {"type": "ready", "seq": 11, "payload": {}},
                ],
            },
        },
        AgentLiveStatus(),
        on_ready=ready,
    )

    assert seq == 11
    ready.assert_called_once_with()


@pytest.mark.anyio
async def test_agent_receive_stops_cleanly_when_listener_is_closed() -> None:
    stop_event = asyncio.Event()
    stop_event.set()
    client = SimpleNamespace(recv_json=AsyncMock())

    with pytest.raises(asyncio.CancelledError):
        await recv_json_or_stop(client, object(), stop_event)


class _WsContext(object):
    def __init__(self) -> None:
        self.entered = asyncio.Event()
        self.exited = asyncio.Event()

    async def __aenter__(self):
        self.entered.set()
        return self

    async def __aexit__(self, _error_type, _error, _traceback) -> None:
        self.exited.set()


def _ws_runtime() -> AgentSessionRuntime:
    return AgentSessionRuntime(
        session_id="agent-session",
        ws_token="token",
        resume_token=None,
        credential=None,
        mind_call_example=None,
        ws_url=None,
        device_id="device-1",
        client_version="1.0.0",
    )


def _waiting_ws_client(context: _WsContext, receive_started: asyncio.Event):
    async def recv_json(_connection):
        receive_started.set()
        await asyncio.Event().wait()

    return SimpleNamespace(
        connect_ws=AsyncMock(return_value=context),
        send_hello=AsyncMock(),
        send_runtime_bind=AsyncMock(),
        recv_json=recv_json,
    )


def _ws_mind() -> SimpleNamespace:
    return SimpleNamespace(
        task_event=asyncio.Event(),
        fresh_pref_config=AsyncMock(return_value={"primary": {}}),
    )


@pytest.mark.anyio
async def test_agent_ready_timeout_closes_websocket_context() -> None:
    context = _WsContext()
    receive_started = asyncio.Event()
    client = _waiting_ws_client(context, receive_started)

    with pytest.raises(TimeoutError, match="did not send ready"):
        await connect_once(
            _ws_mind(),
            client,
            _ws_runtime(),
            AgentLiveStatus(),
            ready_timeout_sec=0.01,
        )

    assert receive_started.is_set()
    assert context.exited.is_set()


@pytest.mark.anyio
async def test_agent_listener_cancellation_closes_websocket_context() -> None:
    context = _WsContext()
    receive_started = asyncio.Event()
    client = _waiting_ws_client(context, receive_started)
    disconnected = Mock()
    task = asyncio.create_task(connect_once(
        _ws_mind(),
        client,
        _ws_runtime(),
        AgentLiveStatus(),
        on_disconnected=disconnected,
    ))
    await receive_started.wait()

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert context.exited.is_set()
    disconnected.assert_called_once_with()
