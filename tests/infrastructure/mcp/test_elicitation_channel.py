import asyncio
import contextlib
import logging
from unittest.mock import AsyncMock

import anyio
import pytest
from mcp import types as mcp_types
from mcp.shared.message import SessionMessage

from agent.domain.mcp_elicitation import (
    ElicitationResponse,
    McpInvocation,
)
from infrastructure.mcp.elicitation_channel import ElicitationChannel
from observability.third_party import route_mcp_protocol_logs


IDENTITY = McpInvocation("session", "turn", "call", "root")


def request(key=1, *, url=None, schema=None):
    params = {"mode": "url", "message": "Open", "url": url, "elicitationId": "website"} if url else {
        "message": "Fill", "requestedSchema": schema or {"type": "object", "properties": {}},
    }
    return SessionMessage(mcp_types.JSONRPCMessage(mcp_types.JSONRPCRequest(
        jsonrpc="2.0", id=key, method="elicitation/create", params=params,
    )))


def cancellation(key):
    return SessionMessage(mcp_types.JSONRPCMessage(mcp_types.JSONRPCNotification(
        jsonrpc="2.0", method="notifications/cancelled", params={"requestId": key},
    )))


@contextlib.asynccontextmanager
async def channel_fixture(handler=None, timeout=1):
    incoming, read = anyio.create_memory_object_stream[SessionMessage | Exception](128)
    write, outgoing = anyio.create_memory_object_stream[SessionMessage](128)
    opener = AsyncMock(return_value=True)
    disconnected = asyncio.Event()
    channel = ElicitationChannel(read, write, server="fixture", handler=handler, open_browser=opener,
        disconnected=disconnected, timeout_sec=timeout)
    forwarded = asyncio.Queue()

    async def receive():
        async for message in channel:
            await forwarded.put(message)

    reader = asyncio.create_task(receive())
    try:
        async with asyncio.timeout(5):
            yield channel, incoming, outgoing, opener, disconnected, forwarded
    finally:
        reader.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await reader
        await channel.aclose()
        await incoming.aclose()
        await write.aclose()
        await outgoing.aclose()


@pytest.mark.anyio
async def test_receiving_other_messages_and_cancelling_one_request_preserves_other_request():
    entered = asyncio.Queue()
    exited = asyncio.Queue()

    async def wait(request):
        await entered.put(request)
        try:
            await asyncio.Event().wait()
        finally:
            await exited.put(request.request_id)

    handler = AsyncMock()
    handler.request_elicitation.side_effect = wait
    async with channel_fixture(handler) as (channel, incoming, outgoing, opener, disconnected, forwarded):
        async with channel.invocation(IDENTITY):
            await incoming.send(request(1))
            first = await entered.get()
            await incoming.send(request(2))
            second = await entered.get()
            passthrough = SessionMessage(mcp_types.JSONRPCMessage(mcp_types.JSONRPCResponse(jsonrpc="2.0", id=99, result={})))
            await incoming.send(passthrough)
            assert await forwarded.get() is passthrough
            await incoming.send(cancellation(1))
            answer = (await outgoing.receive()).message.root
            assert answer.id == 1 and answer.result == {"action": "cancel"}
            assert await exited.get() == first.request_id
            assert exited.empty()
            assert second.invocation == IDENTITY
        assert await exited.get() == second.request_id
        answer = (await outgoing.receive()).message.root
        assert answer.id == 2 and answer.result == {"action": "cancel"}
        assert outgoing.statistics().current_buffer_used == 0
        assert not disconnected.is_set()
        opener.assert_not_awaited()


@pytest.mark.anyio
async def test_cancel_before_request_never_opens_surface():
    handler = AsyncMock()
    async with channel_fixture(handler) as (channel, incoming, outgoing, *rest):
        async with channel.invocation(IDENTITY):
            await incoming.send(cancellation(7))
            await incoming.send(request(7))
            assert (await outgoing.receive()).message.root.result == {"action": "cancel"}
            handler.request_elicitation.assert_not_awaited()


@pytest.mark.anyio
async def test_timeout_and_connection_close_cancel_surface():
    entered = asyncio.Event()
    exited = asyncio.Event()

    async def wait(request):
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            exited.set()

    handler = AsyncMock()
    handler.request_elicitation.side_effect = wait
    async with channel_fixture(handler, timeout=0.05) as (channel, incoming, outgoing, *rest):
        async with channel.invocation(IDENTITY):
            await incoming.send(request())
            await entered.wait()
            assert (await outgoing.receive()).message.root.result == {"action": "cancel"}
            assert exited.is_set()
            entered.clear()
            exited.clear()
            await incoming.send(request(2))
            await entered.wait()
            await channel.aclose()
            assert exited.is_set()
            assert outgoing.statistics().current_buffer_used == 0


@pytest.mark.anyio
@pytest.mark.parametrize("identity", [None, McpInvocation("s", "t", "c", "a", False)])
async def test_no_invocation_or_frozen_no_prompt_policy_declines(identity):
    handler = AsyncMock()
    async with channel_fixture(handler) as (channel, incoming, outgoing, *rest):
        async with channel.invocation(identity):
            await incoming.send(request())
            assert (await outgoing.receive()).message.root.result == {"action": "decline"}
            handler.request_elicitation.assert_not_awaited()


@pytest.mark.anyio
@pytest.mark.parametrize("action,browser_success,expected", [
    ("accept", True, "accept"), ("accept", False, "cancel"), ("decline", True, "decline"), ("cancel", True, "cancel"),
])
async def test_url_navigation_requires_consent(action, browser_success, expected):
    handler = AsyncMock()
    handler.request_elicitation.return_value = ElicitationResponse(action)
    async with channel_fixture(handler) as (channel, incoming, outgoing, opener, *rest):
        opener.return_value = browser_success
        async with channel.invocation(IDENTITY):
            await incoming.send(request(url="https://example.org/authorize?state=private"))
            assert (await outgoing.receive()).message.root.result == {"action": expected}
        if action == "accept":
            opener.assert_awaited_once_with("https://example.org/authorize?state=private")
        else:
            opener.assert_not_awaited()


@pytest.mark.anyio
@pytest.mark.parametrize("message", [
    request(url="javascript:private-data"),
    request(schema={"type": "object", "properties": {"password": {"type": "string", "default": "private-data"}}}),
])
async def test_invalid_input_is_not_presented_and_error_excludes_payload(message):
    handler = AsyncMock()
    async with channel_fixture(handler) as (channel, incoming, outgoing, opener, *rest):
        async with channel.invocation(IDENTITY):
            await incoming.send(message)
            answer = (await outgoing.receive()).message.root
            assert answer.error.code == mcp_types.INVALID_PARAMS
            assert "private-data" not in answer.model_dump_json()
            handler.request_elicitation.assert_not_awaited()
            opener.assert_not_awaited()


def test_overlapping_connections_filter_sdk_payload_and_restore_logging(caplog):
    names = ("mcp.client.sse", "mcp.client.streamable_http", "mcp.shared.session")
    with caplog.at_level(logging.DEBUG), route_mcp_protocol_logs():
        with route_mcp_protocol_logs():
            for name in names:
                logging.getLogger(name).debug("private-answer-and-url")
        for name in names:
            logging.getLogger(name).warning("private-answer-and-url")
    assert "private-answer-and-url" not in caplog.text
    with caplog.at_level(logging.DEBUG):
        logging.getLogger(names[0]).debug("restored")
    assert "restored" in caplog.text


@pytest.mark.anyio
@pytest.mark.parametrize("cause", ["server", "timeout", "call_end"])
async def test_late_accept_after_cancellation_cannot_open_browser_or_send_answer(cause):
    entered = asyncio.Event()
    finished = asyncio.Event()

    async def late_answer(request):
        entered.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            finished.set()
            return ElicitationResponse("accept")

    handler = AsyncMock()
    handler.request_elicitation.side_effect = late_answer
    async with channel_fixture(handler, timeout=0.05 if cause == "timeout" else 1) as (channel, incoming, outgoing, opener, *rest):
        async with channel.invocation(IDENTITY):
            await incoming.send(request(url="https://example.org/authorize"))
            await entered.wait()
            if cause == "server":
                await incoming.send(cancellation(1))
                await incoming.send(cancellation(1))
            if cause != "call_end":
                assert (await outgoing.receive()).message.root.result == {"action": "cancel"}
                await finished.wait()
        if cause == "call_end":
            assert (await outgoing.receive()).message.root.result == {"action": "cancel"}
        opener.assert_not_awaited()
        assert outgoing.statistics().current_buffer_used == 0


@pytest.mark.anyio
async def test_pending_requests_are_bounded_without_cancelling_existing_requests():
    entered = asyncio.Queue()

    async def wait(request):
        await entered.put(request)
        await asyncio.Event().wait()

    handler = AsyncMock()
    handler.request_elicitation.side_effect = wait
    async with channel_fixture(handler) as (channel, incoming, outgoing, *rest):
        async with channel.invocation(IDENTITY):
            for key in range(32):
                await incoming.send(request(key))
                await entered.get()
            await incoming.send(request(32))
            response = (await outgoing.receive()).message.root
            assert response.id == 32 and response.result == {"action": "cancel"}
            assert handler.request_elicitation.await_count == 32


@pytest.mark.anyio
async def test_connection_serializes_distinct_invocations_and_does_not_rebind_waiting_input():
    handler = AsyncMock()
    handler.request_elicitation.return_value = ElicitationResponse("decline")
    second_entered = asyncio.Event()
    second_identity = McpInvocation("session", "other-turn", "other-call", "worker")
    async with channel_fixture(handler) as (channel, incoming, outgoing, *rest):
        async def second_call():
            async with channel.invocation(second_identity):
                second_entered.set()
                await incoming.send(request(2))
                assert (await outgoing.receive()).message.root.id == 2

        async with channel.invocation(IDENTITY):
            second = asyncio.create_task(second_call())
            await incoming.send(request(1))
            assert (await outgoing.receive()).message.root.id == 1
            assert not second_entered.is_set()
        await second
        assert [call.args[0].invocation for call in handler.request_elicitation.await_args_list] == [IDENTITY, second_identity]
