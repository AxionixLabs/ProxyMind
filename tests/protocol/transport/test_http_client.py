# -*- coding: utf-8 -*-

import asyncio
import contextvars
import threading
from unittest.mock import (
    AsyncMock,
    Mock,
)

import httpx
import pytest

from protocol.transport.http_client import open_http_client


@pytest.mark.anyio
async def test_slow_client_construction_keeps_event_loop_responsive() -> None:
    loop = asyncio.get_running_loop()
    entered = asyncio.Event()
    release = threading.Event()
    request_context = contextvars.ContextVar("request_context", default="")
    token = request_context.set("turn_001")
    client = AsyncMock(spec=httpx.AsyncClient)
    client.__aenter__.return_value = client
    client.__aexit__.return_value = None
    factory_threads: list[int] = []
    request_threads: list[int] = []

    def create_client() -> httpx.AsyncClient:
        factory_threads.append(threading.get_ident())
        assert request_context.get() == "turn_001"
        loop.call_soon_threadsafe(entered.set)
        assert release.wait(2.0), "HTTP construction blocked the event loop"
        return client

    async def request() -> None:
        async with open_http_client(create_client) as session:
            request_threads.append(threading.get_ident())
            await session.get("https://example.test/turn/status")

    task = asyncio.create_task(request())
    try:
        await asyncio.wait_for(entered.wait(), timeout=2.0)
        assert not task.done()
        client.__aenter__.assert_not_awaited()
        release.set()
        await asyncio.wait_for(task, timeout=2.0)
    finally:
        release.set()
        await asyncio.gather(task, return_exceptions=True)
        request_context.reset(token)

    assert factory_threads[0] != threading.get_ident()
    assert request_threads == [threading.get_ident()]
    client.get.assert_awaited_once()
    client.__aexit__.assert_awaited_once_with(None, None, None)


@pytest.mark.anyio
@pytest.mark.parametrize("construction_fails", [False, True])
async def test_repeated_cancellation_during_construction_closes_without_sending(
    construction_fails: bool,
) -> None:
    loop = asyncio.get_running_loop()
    entered = asyncio.Event()
    release = threading.Event()
    client = AsyncMock(spec=httpx.AsyncClient)
    client.__aenter__.return_value = client
    client.__aexit__.return_value = None

    def create_client() -> httpx.AsyncClient:
        loop.call_soon_threadsafe(entered.set)
        assert release.wait(2.0)
        if construction_fails:
            raise OSError("certificate file is unavailable")
        return client

    async def request() -> None:
        async with open_http_client(create_client) as session:
            await session.post("https://example.test/mind-chat")

    task = asyncio.create_task(request())
    try:
        await asyncio.wait_for(entered.wait(), timeout=2.0)
        task.cancel()
        await asyncio.sleep(0)
        task.cancel()
        await asyncio.sleep(0)
        assert not task.done()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, timeout=2.0)
    finally:
        release.set()
        await asyncio.gather(task, return_exceptions=True)

    client.post.assert_not_awaited()
    if construction_fails:
        client.__aenter__.assert_not_awaited()
        client.__aexit__.assert_not_awaited()
    else:
        client.__aenter__.assert_awaited_once()
        client.__aexit__.assert_awaited_once_with(None, None, None)


@pytest.mark.anyio
async def test_construction_failure_propagates() -> None:
    factory = Mock(side_effect=OSError("certificate file is unavailable"))

    with pytest.raises(OSError, match="certificate file is unavailable"):
        async with open_http_client(factory):
            pytest.fail("failed client must not be used")


@pytest.mark.anyio
@pytest.mark.parametrize("cancel", [False, True])
async def test_request_failure_and_cancellation_close_client(cancel: bool) -> None:
    entered = asyncio.Event()
    client = AsyncMock(spec=httpx.AsyncClient)
    client.__aenter__.return_value = client
    client.__aexit__.return_value = None

    async def request() -> None:
        async with open_http_client(lambda: client):
            entered.set()
            if cancel:
                await asyncio.Event().wait()
            raise httpx.ConnectError("offline")

    task = asyncio.create_task(request())
    try:
        await asyncio.wait_for(entered.wait(), timeout=2.0)
        if cancel:
            task.cancel()
        error_type = asyncio.CancelledError if cancel else httpx.ConnectError
        with pytest.raises(error_type):
            await asyncio.wait_for(task, timeout=2.0)
    finally:
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    client.__aexit__.assert_awaited_once()
    assert client.__aexit__.await_args.args[0] is error_type
