import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import httpx
import pytest
from websockets.datastructures import Headers
from websockets.exceptions import InvalidStatus
from websockets.http11 import Response

from frontends.subscription.client import AgentClient
from frontends.subscription.loop import AgentConnection, AgentSupervisor
from frontends.subscription.models import AgentConfig, AgentLiveStatus, AgentSessionRuntime
from frontends.subscription.ws import AgentWsProtocolError, connection_scope, handle_server_message


def session_runtime():
    return AgentSessionRuntime(
        session_id="session-1", ws_token="old-ws", resume_token="old-resume",
        credential="credential", web_url=None, ws_url=None,
        device_id="device-1", client_version="1.0", last_acked_seq=7,
        ready_received=True, forwarded_message_ids={"message-1"},
    )


def http_error(status, code):
    request = httpx.Request("POST", "https://example.test/agents/resume")
    response = httpx.Response(status, request=request, json={"details": {"code": code}})
    return httpx.HTTPStatusError("request failed", request=request, response=response)


def connection_for(client):
    return AgentConnection(
        SimpleNamespace(lifecycle=SimpleNamespace(stop_event=asyncio.Event())),
        client,
        AgentConfig("https://example.test", "device-1", "agent-1", "1.0", "linux", "x64"),
        AgentLiveStatus(),
    )


@pytest.mark.anyio
@pytest.mark.parametrize("status,code", [
    (401, "AGENT_TOKEN_INVALID"),
    (401, "AGENT_RESUME_SCOPE_INVALID"),
    (401, "AGENT_RESUME_SESSION_MISMATCH"),
    (401, "AGENT_RESUME_DEVICE_MISMATCH"),
    (404, "AGENT_SESSION_NOT_FOUND"),
])
async def test_invalid_resume_credentials_are_discarded_before_reopening(status, code):
    old = session_runtime()
    fresh = session_runtime()
    fresh.session_id = "session-2"
    client = Mock(spec=AgentClient)
    client.resume_session = AsyncMock(side_effect=http_error(status, code))
    connection = connection_for(client)

    async def reopen(*, previous):
        assert previous is old
        assert previous.ws_token is None
        assert previous.resume_token is None
        assert previous.last_acked_seq == 7
        assert previous.forwarded_message_ids == {"message-1"}
        return fresh

    connection.open_session_runtime = AsyncMock(side_effect=reopen)
    assert await connection.resume_or_open(old) is fresh
    connection.open_session_runtime.assert_awaited_once_with(previous=old)


@pytest.mark.anyio
async def test_missing_resume_token_opens_a_fresh_session():
    old = session_runtime()
    old.resume_token = None
    fresh = session_runtime()
    client = Mock(spec=AgentClient)
    connection = connection_for(client)
    connection.open_session_runtime = AsyncMock(return_value=fresh)

    assert await connection.resume_or_open(old) is fresh
    assert old.ws_token is None
    connection.open_session_runtime.assert_awaited_once_with(previous=old)
    client.resume_session.assert_not_called()


@pytest.mark.anyio
async def test_resume_adopts_both_new_tokens_and_preserves_confirmation_state():
    old = session_runtime()
    client = Mock(spec=AgentClient)
    client.unwrap_data.side_effect = AgentClient.unwrap_data
    client.resume_session = AsyncMock(return_value={"data": {
        "resumable": True, "session_id": "session-1", "ws_token": "new-ws",
        "resume_token": "new-resume", "ws_url": "wss://example.test/agents/ws",
    }})
    connection = connection_for(client)
    connection.open_session_runtime = AsyncMock()

    recovered = await connection.resume_or_open(old)
    assert (recovered.ws_token, recovered.resume_token) == ("new-ws", "new-resume")
    assert recovered.session_id == old.session_id
    assert recovered.last_acked_seq == 7
    assert recovered.forwarded_message_ids is old.forwarded_message_ids
    assert recovered.credential == old.credential
    assert old.ws_token is None
    connection.open_session_runtime.assert_not_awaited()


@pytest.mark.anyio
@pytest.mark.parametrize("failure", [
    http_error(503, "UNAVAILABLE"),
    http_error(429, "RATE_LIMITED"),
    httpx.ConnectError("unavailable"),
    TimeoutError("timeout"),
])
async def test_recovery_retries_before_returning_a_usable_handshake(monkeypatch, failure):
    old = session_runtime()
    fresh = session_runtime()
    fresh.ws_token = "new-ws"
    delay = AsyncMock()
    monkeypatch.setattr("frontends.subscription.loop.sleep_or_stop", delay)
    connection = SimpleNamespace(resume_or_open=AsyncMock(side_effect=[failure, fresh]))
    stop_event = asyncio.Event()
    supervisor = AgentSupervisor(
        SimpleNamespace(lifecycle=SimpleNamespace(stop_event=stop_event)), connection, AgentLiveStatus(),
    )

    assert await supervisor.handle_resume(old) is fresh
    assert old.ws_token is None
    assert old.resume_token == "old-resume"
    assert connection.resume_or_open.await_count == 2
    delay.assert_awaited_once_with(2.0, stop_event)


@pytest.mark.anyio
@pytest.mark.parametrize("status,code", [
    (401, "AGENT_CLIENT_UNAUTHORIZED"), (403, "FORBIDDEN"),
    (404, "UNKNOWN_ROUTE"), (422, "request_validation"),
])
async def test_unrecoverable_http_errors_stop_without_reopening(monkeypatch, status, code):
    old = session_runtime()
    error = http_error(status, code)
    client = Mock(spec=AgentClient)
    client.resume_session = AsyncMock(side_effect=error)
    connection = connection_for(client)
    connection.open_session_runtime = AsyncMock()
    delay = AsyncMock()
    monkeypatch.setattr("frontends.subscription.loop.sleep_or_stop", delay)
    supervisor = AgentSupervisor(connection.host, connection, AgentLiveStatus())

    with pytest.raises(httpx.HTTPStatusError) as failed:
        await supervisor.handle_resume(old)
    assert failed.value is error
    connection.open_session_runtime.assert_not_awaited()
    delay.assert_not_awaited()


@pytest.mark.anyio
async def test_recovery_backoff_is_bounded_and_stop_interrupts_retry(monkeypatch):
    old = session_runtime()
    stop_event = asyncio.Event()
    delays = []

    async def delay(seconds, event):
        delays.append(seconds)
        if len(delays) == 6:
            event.set()

    monkeypatch.setattr("frontends.subscription.loop.sleep_or_stop", delay)
    connection = SimpleNamespace(resume_or_open=AsyncMock(side_effect=httpx.ConnectError("offline")))
    supervisor = AgentSupervisor(
        SimpleNamespace(lifecycle=SimpleNamespace(stop_event=stop_event)), connection, AgentLiveStatus(),
    )
    with pytest.raises(asyncio.CancelledError):
        await supervisor.handle_resume(old)
    assert delays == [2.0, 4.0, 8.0, 16.0, 30.0, 30.0]
    assert connection.resume_or_open.await_count == 6


@pytest.mark.anyio
async def test_failed_handshake_consumes_cached_ticket_and_notifies_disconnect():
    runtime = session_runtime()
    client = Mock(spec=AgentClient)
    client.connect_ws = AsyncMock(side_effect=OSError("offline"))
    disconnected = Mock()
    with pytest.raises(OSError):
        async with connection_scope(client, runtime, on_disconnected=disconnected):
            pytest.fail("connection must fail")
    assert runtime.ws_token is None
    assert runtime.resume_token == "old-resume"
    client.connect_ws.assert_awaited_once_with(session_id="session-1", ws_token="old-ws", ws_base_url=None)
    disconnected.assert_called_once_with()


@pytest.mark.anyio
@pytest.mark.parametrize("failure", [
    OSError("disconnected"),
    InvalidStatus(Response(401, "Unauthorized", Headers())),
    InvalidStatus(Response(500, "Internal Server Error", Headers())),
])
async def test_supervisor_waits_for_recovery_before_next_websocket(monkeypatch, failure):
    old = session_runtime()
    fresh = session_runtime()
    fresh.ws_token = "new-ws"
    attempts = []
    monkeypatch.setattr("frontends.subscription.loop.sleep_or_stop", AsyncMock())

    async def connect(runtime):
        attempts.append(runtime.ws_token)
        if len(attempts) == 1:
            raise failure

    connection = SimpleNamespace(
        open_session_runtime=AsyncMock(return_value=old),
        resume_or_open=AsyncMock(side_effect=[httpx.ConnectError("offline"), fresh]),
        connect_once=connect,
    )
    supervisor = AgentSupervisor(
        SimpleNamespace(lifecycle=SimpleNamespace(stop_event=asyncio.Event())), connection, AgentLiveStatus(),
    )
    await supervisor.run()
    assert attempts == ["old-ws", "new-ws"]
    assert supervisor.runtime is fresh


@pytest.mark.anyio
async def test_reopen_failure_does_not_restore_rejected_resume_credentials(monkeypatch):
    old = session_runtime()
    fresh = session_runtime()
    client = Mock(spec=AgentClient)
    client.resume_session = AsyncMock(side_effect=http_error(401, "AGENT_TOKEN_INVALID"))
    connection = connection_for(client)
    connection.open_session_runtime = AsyncMock(side_effect=[http_error(503, "UNAVAILABLE"), fresh])
    monkeypatch.setattr("frontends.subscription.loop.sleep_or_stop", AsyncMock())
    supervisor = AgentSupervisor(connection.host, connection, AgentLiveStatus())

    assert await supervisor.handle_resume(old) is fresh
    client.resume_session.assert_awaited_once()
    assert connection.open_session_runtime.await_count == 2
    assert old.resume_token is None
    assert old.ws_token is None


@pytest.mark.anyio
async def test_cancel_interrupts_recovery_request_without_retrying():
    started = asyncio.Event()

    async def resume(_runtime):
        started.set()
        await asyncio.Event().wait()

    connection = SimpleNamespace(resume_or_open=AsyncMock(side_effect=resume))
    supervisor = AgentSupervisor(
        SimpleNamespace(lifecycle=SimpleNamespace(stop_event=asyncio.Event())), connection, AgentLiveStatus(),
    )
    task = asyncio.create_task(supervisor.handle_resume(session_runtime()))
    try:
        await asyncio.wait_for(started.wait(), 1)
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    connection.resume_or_open.assert_awaited_once()


@pytest.mark.anyio
@pytest.mark.parametrize("code,action", [
    ("AGENT_TOKEN_INVALID", "reconnect"),
    ("AGENT_WS_SESSION_MISMATCH", "reconnect"),
    ("AGENT_SESSION_NOT_FOUND", "reopen"),
    ("AGENT_CLIENT_UNAUTHORIZED", "abort"),
])
async def test_websocket_error_codes_choose_credential_recovery(code, action):
    with pytest.raises(AgentWsProtocolError) as failed:
        await handle_server_message(
            SimpleNamespace(), Mock(spec=AgentClient), Mock(), session_runtime(),
            {"type": "error", "payload": {"code": code, "message": "rejected"}}, AgentLiveStatus(),
        )
    assert failed.value.action == action


@pytest.mark.anyio
async def test_malformed_resume_response_stops_without_reconnecting(monkeypatch):
    error = ValueError("invalid resume response")
    connection = SimpleNamespace(resume_or_open=AsyncMock(side_effect=error))
    supervisor = AgentSupervisor(
        SimpleNamespace(lifecycle=SimpleNamespace(stop_event=asyncio.Event())), connection, AgentLiveStatus(),
    )
    delay = AsyncMock()
    monkeypatch.setattr("frontends.subscription.loop.sleep_or_stop", delay)
    with pytest.raises(ValueError, match="invalid resume response"):
        await supervisor.handle_resume(session_runtime())
    delay.assert_not_awaited()
