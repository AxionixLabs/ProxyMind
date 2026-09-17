# -*- coding: utf-8 -*-

import asyncio

import httpx
import pytest
from unittest.mock import AsyncMock

from agent.domain.mcp_oauth import (
    MCP_OAUTH_CALLBACK_MAX_BYTES,
    McpOAuthError,
)
from agent.ports.mcp_oauth import McpOAuthCallbackInput
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


@pytest.mark.anyio
@pytest.mark.parametrize("change", [
    "origin", "scheme", "port", "userinfo", "path", "fragment", "empty_fragment", "duplicate",
    "state", "missing_state", "issuer", "missing_issuer", "code_error", "empty_code", "control", "percent", "encoding", "oversize",
])
async def test_pasted_callback_rejects_wrong_binding_and_malformed_parameters(change):
    async with oauth_callback(port=None, state="expected", issuer=ISSUER_URL, require_issuer=True) as callback:
        valid = httpx.URL(callback.redirect_uri).copy_merge_params({"code": "private-code", "state": "expected", "iss": ISSUER_URL})
        variants = {
            "origin": str(valid.copy_with(host="attacker.test")), "scheme": str(valid.copy_with(scheme="https")),
            "port": str(valid.copy_with(port=1)), "userinfo": str(valid.copy_with(username="private-user")),
            "path": str(valid.copy_with(path="/other")), "fragment": str(valid) + "#private-code", "empty_fragment": str(valid) + "#",
            "duplicate": str(valid) + "&state=expected", "state": str(valid.copy_set_param("state", "wrong")),
            "missing_state": str(valid.copy_remove_param("state")), "issuer": str(valid.copy_set_param("iss", "https://wrong.test")),
            "missing_issuer": str(valid.copy_remove_param("iss")), "code_error": str(valid) + "&error=access_denied",
            "empty_code": str(valid.copy_set_param("code", "")), "control": str(valid).replace("callback", "call\tback"),
            "percent": str(valid) + "&extra=%xx", "encoding": str(valid) + "&extra=%ff", "oversize": str(valid) + "x" * MCP_OAUTH_CALLBACK_MAX_BYTES,
        }
        reader = AsyncMock(spec=McpOAuthCallbackInput)
        reader.read_callback.return_value = variants[change]
        with pytest.raises(McpOAuthError) as error:
            await callback.wait(reader)
        assert error.value.code == ("callback_input_too_long" if change == "oversize" else "invalid_response")
        assert "private-" not in str(error.value)
    await assert_callback_closed(callback.redirect_uri)


@pytest.mark.anyio
async def test_manual_success_and_provider_denial_share_one_time_receiver():
    for denied in (False, True):
        async with oauth_callback(port=None, state="expected", issuer=ISSUER_URL, require_issuer=False) as callback:
            url = str(httpx.URL(callback.redirect_uri).copy_merge_params({"state": "expected", "error" if denied else "code": "access_denied" if denied else "private-code"}))
            reader = AsyncMock(spec=McpOAuthCallbackInput)
            reader.read_callback.return_value = url
            if denied:
                with pytest.raises(McpOAuthError) as error:
                    await callback.wait(reader)
                assert error.value.code == "authorization_denied"
            else:
                assert await callback.wait(reader) == "private-code"
            assert await visit_callback(url) == 409


@pytest.mark.anyio
@pytest.mark.parametrize("ending", ["http", "cancel", "restore_failure"])
async def test_waiting_input_is_closed_when_loopback_wins_or_login_is_cancelled(ending):
    entered, closed = asyncio.Event(), asyncio.Event()

    async def read_callback(*, max_bytes):
        assert max_bytes == MCP_OAUTH_CALLBACK_MAX_BYTES
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            closed.set()
            if ending == "restore_failure":
                raise McpOAuthError("callback_input_unavailable")

    reader = AsyncMock(spec=McpOAuthCallbackInput)
    reader.read_callback.side_effect = read_callback
    async with oauth_callback(port=None, state="expected", issuer=ISSUER_URL, require_issuer=False) as callback:
        task = asyncio.create_task(callback.wait(reader))
        try:
            await asyncio.wait_for(entered.wait(), 2)
            if ending in ("http", "restore_failure"):
                url = str(httpx.URL(callback.redirect_uri).copy_merge_params({"state": "expected", "code": "private-code"}))
                assert await visit_callback(url) == 200
                if ending == "restore_failure":
                    with pytest.raises(McpOAuthError) as error:
                        await task
                    assert error.value.code == "callback_input_unavailable"
                else:
                    assert await task == "private-code"
            else:
                task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await task
            assert closed.is_set()
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
    await assert_callback_closed(callback.redirect_uri)
