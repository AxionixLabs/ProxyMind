# -*- coding: utf-8 -*-

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from mind_app.controller import Mind
from mind_app.subscription.forwarding import AgentInbox
from mind_app.subscription.models import (
    AgentConfig,
    AgentForwardRequest,
    AgentSessionRuntime,
)
from mind_app.subscription.runtime import AgentRuntime
from mind_app.subscription.loop import AgentSupervisor


def _config() -> AgentConfig:
    return AgentConfig(
        base_url="https://example.test",
        device_id="device-1",
        agent_id="agent-1",
        client_version="1.0.0",
        platform="darwin",
        arch="arm64",
    )


def _request(message_id: str = "message-1") -> AgentForwardRequest:
    return AgentForwardRequest(
        message_id=message_id,
        call_id=f"call-{message_id}",
        cid="cid-1",
        sid="sid-1",
        payload={"message": "inspect workspace"},
    )


def _session_runtime() -> AgentSessionRuntime:
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


@pytest.mark.anyio
async def test_agent_runtime_starts_once_and_stops_listener() -> None:
    started = asyncio.Event()
    cancelled = asyncio.Event()

    async def run() -> None:
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    runtime = AgentRuntime(
        SimpleNamespace(),
        config=_config(),
        client=SimpleNamespace(),
        supervisor=SimpleNamespace(run=run),
    )

    first = runtime.start_background()
    second = runtime.start_background()
    await started.wait()

    assert first is second
    assert runtime.is_running()

    await runtime.stop()

    assert cancelled.is_set()
    assert not runtime.is_running()


@pytest.mark.anyio
async def test_agent_runtime_consumes_background_failure() -> None:
    async def run() -> None:
        raise RuntimeError("listener failed")

    runtime = AgentRuntime(
        SimpleNamespace(),
        config=_config(),
        client=SimpleNamespace(),
        supervisor=SimpleNamespace(run=run),
    )
    labels = []
    runtime.bind_inbox_changed(lambda: labels.append(runtime.status_label()))

    task = runtime.start_background()
    results = await asyncio.gather(task, return_exceptions=True)
    await asyncio.sleep(0)

    assert isinstance(results[0], RuntimeError)
    assert not runtime.is_running()
    assert labels == ["agent · off", "agent · reconnecting", "agent · off"]


@pytest.mark.anyio
async def test_agent_runtime_waits_for_current_listener_ready() -> None:
    release = asyncio.Event()

    async def run() -> None:
        await release.wait()

    runtime = AgentRuntime(
        SimpleNamespace(),
        config=_config(),
        client=SimpleNamespace(),
        supervisor=SimpleNamespace(run=run),
    )

    runtime.start_background()
    waiting = asyncio.create_task(runtime.wait_until_ready())
    await asyncio.sleep(0)

    assert not waiting.done()
    assert not runtime.is_ready()

    runtime._mark_ready()
    await waiting

    assert runtime.is_ready()
    await runtime.stop()


@pytest.mark.anyio
async def test_agent_runtime_ready_wait_has_a_bounded_timeout() -> None:
    async def run() -> None:
        await asyncio.Event().wait()

    runtime = AgentRuntime(
        SimpleNamespace(),
        config=_config(),
        client=SimpleNamespace(),
        supervisor=SimpleNamespace(run=run),
    )
    runtime.start_background()

    with pytest.raises(TimeoutError, match="not ready within 0.01s"):
        await runtime.wait_until_ready(timeout_sec=0.01)

    assert runtime.is_running()
    await runtime.stop()


@pytest.mark.anyio
async def test_agent_runtime_disconnect_clears_connection_readiness() -> None:
    async def run() -> None:
        await asyncio.Event().wait()

    runtime = AgentRuntime(
        SimpleNamespace(),
        config=_config(),
        client=SimpleNamespace(),
        supervisor=SimpleNamespace(run=run),
    )
    runtime.start_background()
    runtime._mark_ready()

    assert runtime.is_ready()

    runtime._mark_disconnected()

    assert not runtime.is_ready()
    assert runtime.status_label() == "agent · reconnecting"
    await runtime.stop()


@pytest.mark.anyio
async def test_agent_runtime_ready_wait_survives_transient_disconnect() -> None:
    async def run() -> None:
        await asyncio.Event().wait()

    runtime = AgentRuntime(
        SimpleNamespace(),
        config=_config(),
        client=SimpleNamespace(),
        supervisor=SimpleNamespace(run=run),
    )
    runtime.start_background()
    waiting = asyncio.create_task(runtime.wait_until_ready(timeout_sec=1.0))
    await asyncio.sleep(0)

    runtime._mark_ready()
    runtime._mark_disconnected()
    await asyncio.sleep(0)

    assert not waiting.done()

    runtime._mark_ready()
    await waiting
    await runtime.stop()


def test_agent_runtime_rebinds_pending_messages_to_current_connection() -> None:
    inbox = AgentInbox()
    item = inbox.add(_request())
    runtime = AgentRuntime(
        SimpleNamespace(),
        config=_config(),
        client=SimpleNamespace(),
        inbox=inbox,
        supervisor=SimpleNamespace(run=AsyncMock()),
    )
    connection = object()
    session = _session_runtime()
    live_status = runtime.live_status

    runtime._rebind_pending_contexts(
        runtime.client,
        connection,
        session,
        live_status,
    )

    context = runtime.contexts[item.request.message_id]
    assert context.client is runtime.client
    assert context.connection is connection
    assert context.runtime is session
    assert context.live_status is live_status


@pytest.mark.anyio
async def test_agent_supervisor_resumes_session_after_pause(monkeypatch) -> None:
    first_runtime = _session_runtime()
    first_runtime.resume_token = "resume-token"
    resumed_runtime = _session_runtime()
    resumed_runtime.resume_token = "next-resume-token"
    attempts = []
    connected = asyncio.Event()

    async def connect_once(_runtime) -> None:
        attempts.append(_runtime)
        connected.set()
        await asyncio.Event().wait()

    connection = SimpleNamespace(
        open_session_runtime=AsyncMock(return_value=first_runtime),
        resume_or_open=AsyncMock(return_value=resumed_runtime),
        connect_once=connect_once,
    )
    monkeypatch.setattr(
        "mind_app.subscription.loop.publish_external_access",
        AsyncMock(),
    )
    supervisor = AgentSupervisor(
        SimpleNamespace(task_event=asyncio.Event()),
        connection,
        SimpleNamespace(update=Mock()),
    )

    first = asyncio.create_task(supervisor.run())
    await connected.wait()
    first.cancel()
    with pytest.raises(asyncio.CancelledError):
        await first

    connected.clear()
    second = asyncio.create_task(supervisor.run())
    await connected.wait()

    assert attempts == [first_runtime, resumed_runtime]
    connection.open_session_runtime.assert_awaited_once_with()
    connection.resume_or_open.assert_awaited_once_with(first_runtime)
    assert supervisor.runtime is resumed_runtime

    second.cancel()
    with pytest.raises(asyncio.CancelledError):
        await second


@pytest.mark.anyio
async def test_agent_supervisor_retries_ready_timeout_as_pre_ready_failure(
    monkeypatch,
) -> None:
    runtime = _session_runtime()
    delay = AsyncMock()
    monkeypatch.setattr(
        "mind_app.subscription.loop.sleep_or_stop",
        delay,
    )
    live_status = SimpleNamespace(update=Mock())
    task_event = asyncio.Event()
    supervisor = AgentSupervisor(
        SimpleNamespace(task_event=task_event),
        SimpleNamespace(),
        live_status,
    )

    recovered = await supervisor.handle_disconnect(
        runtime,
        TimeoutError("server did not send ready"),
    )

    assert recovered is runtime
    assert runtime.pre_ready_connect_failures == 1
    live_status.update.assert_any_call(
        "Retrying Link",
        "Handshake not ready yet · retrying WS in 2s",
    )
    delay.assert_awaited_once_with(2.0, task_event)


@pytest.mark.anyio
async def test_agent_runtime_propagates_failure_before_ready() -> None:
    async def run() -> None:
        await asyncio.sleep(0)
        raise RuntimeError("listener failed before ready")

    runtime = AgentRuntime(
        SimpleNamespace(),
        config=_config(),
        client=SimpleNamespace(),
        supervisor=SimpleNamespace(run=run),
    )

    runtime.start_background()

    with pytest.raises(RuntimeError, match="listener failed before ready"):
        await runtime.wait_until_ready()

    assert not runtime.is_ready()


@pytest.mark.anyio
async def test_agent_runtime_restart_requires_a_new_ready_message() -> None:
    blocker = asyncio.Event()

    async def run() -> None:
        await blocker.wait()

    runtime = AgentRuntime(
        SimpleNamespace(),
        config=_config(),
        client=SimpleNamespace(),
        supervisor=SimpleNamespace(run=run),
    )

    runtime.start_background()
    runtime._mark_ready()
    await runtime.wait_until_ready()
    await runtime.stop()

    assert not runtime.is_ready()

    runtime.start_background()
    waiting = asyncio.create_task(runtime.wait_until_ready())
    await asyncio.sleep(0)

    assert not waiting.done()

    runtime._mark_ready()
    await waiting
    await runtime.stop()


@pytest.mark.anyio
async def test_agent_runtime_runs_named_message_and_releases_local_state() -> None:
    inbox = AgentInbox()
    item = inbox.add(_request())
    executor = SimpleNamespace(execute=AsyncMock())
    runtime = AgentRuntime(
        SimpleNamespace(),
        config=_config(),
        client=SimpleNamespace(),
        inbox=inbox,
        executor=executor,
        supervisor=SimpleNamespace(run=AsyncMock()),
    )
    runtime.remember_context(
        item,
        SimpleNamespace(),
        object(),
        _session_runtime(),
        runtime.live_status,
    )
    snapshots = []
    runtime.bind_inbox_changed(
        lambda: snapshots.append(tuple(entry.status for entry in inbox.items))
    )

    completed = await runtime.run_message(
        "message-1",
        turn_id="turn_remote",
    )

    assert completed is item
    assert completed.status == "completed"
    executor.execute.assert_awaited_once()
    assert executor.execute.await_args.kwargs["turn_id"] == "turn_remote"
    assert inbox.items == []
    assert runtime.contexts == {}
    assert ("running",) in snapshots
    assert snapshots[-1] == ()


@pytest.mark.anyio
async def test_agent_runtime_discards_message_when_execution_context_is_missing() -> None:
    inbox = AgentInbox()
    inbox.add(_request())
    runtime = AgentRuntime(
        SimpleNamespace(),
        config=_config(),
        client=SimpleNamespace(),
        inbox=inbox,
        supervisor=SimpleNamespace(run=AsyncMock()),
    )

    with pytest.raises(RuntimeError, match="context missing"):
        await runtime.run_message("message-1")

    assert inbox.items == []
    assert runtime.contexts == {}


@pytest.mark.anyio
async def test_agent_runtime_discards_message_without_remote_execution() -> None:
    inbox = AgentInbox()
    item = inbox.add(_request())
    runtime = AgentRuntime(
        SimpleNamespace(),
        config=_config(),
        client=SimpleNamespace(),
        inbox=inbox,
        supervisor=SimpleNamespace(run=AsyncMock()),
    )
    runtime.remember_context(
        item,
        SimpleNamespace(),
        object(),
        _session_runtime(),
        runtime.live_status,
    )

    runtime.status_outbox.cancelled = AsyncMock()

    removed = await runtime.discard("message-1")

    assert removed is item
    assert inbox.items == []
    assert runtime.contexts == {}
    runtime.status_outbox.cancelled.assert_awaited_once_with(
        item.request,
        session_id="agent-session",
        reason="message_deleted",
    )


@pytest.mark.anyio
async def test_agent_runtime_interrupted_message_is_not_left_running() -> None:
    inbox = AgentInbox()
    item = inbox.add(_request())
    started = asyncio.Event()

    async def execute(*_args, **_kwargs) -> None:
        started.set()
        await asyncio.Future()

    runtime = AgentRuntime(
        SimpleNamespace(),
        config=_config(),
        client=SimpleNamespace(),
        inbox=inbox,
        executor=SimpleNamespace(execute=execute),
        supervisor=SimpleNamespace(run=AsyncMock()),
    )
    runtime.remember_context(
        item,
        SimpleNamespace(),
        object(),
        _session_runtime(),
        runtime.live_status,
    )

    task = asyncio.create_task(runtime.run_message("message-1"))
    await started.wait()

    assert item.status == "running"

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert inbox.items == []
    assert runtime.contexts == {}


@pytest.mark.anyio
async def test_controller_pauses_then_reuses_and_releases_subscription_listener(
    monkeypatch,
) -> None:
    listener = SimpleNamespace(
        start_background=Mock(),
        bind_inbox_changed=Mock(),
        stop=AsyncMock(),
        shutdown=AsyncMock(),
    )
    factory = Mock(return_value=listener)
    monkeypatch.setattr(
        "mind_app.subscription.runtime.AgentRuntime",
        factory,
    )
    controller = object.__new__(Mind)
    controller.subscription_runtime = None

    first = controller.start_subscription_listener()
    second = controller.start_subscription_listener()

    assert first is second is listener
    factory.assert_called_once_with(controller)
    assert listener.start_background.call_count == 2

    await controller.pause_subscription_listener()

    assert controller.subscription_runtime is listener
    listener.stop.assert_awaited_once_with()
    listener.bind_inbox_changed.assert_not_called()

    assert controller.start_subscription_listener() is listener

    await controller.stop_subscription_listener()

    assert controller.subscription_runtime is None
    listener.bind_inbox_changed.assert_called_once_with(None)
    listener.shutdown.assert_awaited_once_with()
