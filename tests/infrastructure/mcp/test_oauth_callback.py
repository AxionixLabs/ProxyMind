# -*- coding: utf-8 -*-

import asyncio

import httpx
import pytest

from agent.domain.mcp_oauth import McpOAuthError
from infrastructure.mcp.oauth_callback import oauth_callback
from tests.infrastructure.mcp.oauth_browser import (
    assert_callback_closed,
    visit_callback,
)
from tests.infrastructure.mcp.oauth_fixture import ISSUER_URL


@pytest.mark.anyio
async def test_wrong_route_state_duplicate_parameters_and_duplicate_callback() -> None:
    async with oauth_callback(port=None, state="expected", issuer=ISSUER_URL, require_issuer=True) as callback:
        valid = httpx.URL(callback.redirect_uri).copy_merge_params({"state": "expected", "code": "private-code", "iss": ISSUER_URL})
        assert await visit_callback(str(valid.copy_with(path="/favicon.ico"))) == 404
        assert await visit_callback(str(valid.copy_set_param("state", "wrong"))) == 400
        assert await visit_callback(str(valid.copy_add_param("state", "expected"))) == 400
        assert await visit_callback(str(valid)) == 200
        assert await callback.wait() == "private-code"
        assert await visit_callback(str(valid)) == 409
    await assert_callback_closed(callback.redirect_uri)


@pytest.mark.anyio
@pytest.mark.parametrize("issuer", [None, "https://wrong.example/issuer"])
async def test_required_issuer_missing_or_mismatched_is_terminal(issuer: str | None) -> None:
    async with oauth_callback(port=None, state="expected", issuer=ISSUER_URL, require_issuer=True) as callback:
        url = httpx.URL(callback.redirect_uri).copy_merge_params({"state": "expected", "code": "private-code"})
        if issuer is not None:
            url = url.copy_add_param("iss", issuer)
        assert await visit_callback(str(url)) == 400
        with pytest.raises(McpOAuthError) as error:
            await callback.wait()
        assert error.value.code == "invalid_response"


@pytest.mark.anyio
async def test_wrong_host_does_not_consume_callback_and_code_error_combination_is_rejected() -> None:
    async with oauth_callback(port=None, state="expected", issuer=ISSUER_URL, require_issuer=False) as callback:
        parsed = httpx.URL(callback.redirect_uri)
        assert parsed.port is not None
        reader, writer = await asyncio.open_connection(parsed.host, parsed.port)
        try:
            writer.write(b"GET /callback?state=expected&code=private-code HTTP/1.1\r\nHost: attacker.test\r\n\r\n")
            await writer.drain()
            response = await reader.read()
            assert response.startswith(b"HTTP/1.1 400")
            assert b"private-code" not in response
        finally:
            writer.close()
            await writer.wait_closed()
        ambiguous = parsed.copy_merge_params({"state": "expected", "code": "private-code", "error": ""})
        assert await visit_callback(str(ambiguous)) == 400
        with pytest.raises(McpOAuthError):
            await callback.wait()


@pytest.mark.anyio
async def test_busy_port_and_cancellation_with_incomplete_connection() -> None:
    entered = asyncio.Event()
    address: list[str] = []
    connections: list[tuple[asyncio.StreamReader, asyncio.StreamWriter]] = []

    async def login() -> None:
        async with oauth_callback(port=None, state="expected", issuer=ISSUER_URL, require_issuer=False) as callback:
            address.append(callback.redirect_uri)
            parsed = httpx.URL(callback.redirect_uri)
            assert parsed.port is not None
            with pytest.raises(McpOAuthError) as error:
                async with oauth_callback(port=parsed.port, state="other", issuer=ISSUER_URL, require_issuer=False):
                    pytest.fail("Duplicate listener was accepted")
            assert error.value.code == "callback_unavailable"
            reader, writer = await asyncio.open_connection(parsed.host, parsed.port)
            connections.append((reader, writer))
            writer.write(b"GET /callback HTTP/1.1\r\n")
            await writer.drain()
            entered.set()
            await callback.wait()

    task = asyncio.create_task(login())
    await asyncio.wait_for(entered.wait(), 2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    await assert_callback_closed(address[0])
    reader, writer = connections[0]
    assert await asyncio.wait_for(reader.read(), 2) == b""
    writer.close()
    await writer.wait_closed()
