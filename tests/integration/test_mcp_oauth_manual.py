# -*- coding: utf-8 -*-

import asyncio
from pathlib import Path
from unittest.mock import (
    AsyncMock,
    Mock,
    patch,
)

import httpx
import pytest

from agent.application.mcp.oauth import McpOAuthService
from agent.domain.mcp_oauth import (
    McpOAuthError,
    McpOAuthLoginRequest,
    McpOAuthStorageError,
    McpOAuthTarget,
)
from agent.ports.mcp_oauth import (
    McpOAuthCallbackInput,
    McpOAuthPresenter,
)
from infrastructure.mcp.oauth_adapter import McpOAuthAdapter
from infrastructure.mcp.oauth_credentials import SystemMcpCredentialStore
from tests.fakes.mcp_credentials import MemoryVault
from tests.infrastructure.mcp.oauth_browser import (
    assert_callback_closed,
    visit_callback,
)
from tests.infrastructure.mcp.oauth_fixture import (
    OAuthService,
    RESOURCE_URL,
    TOKEN_URL,
)
from tests.integration.test_mcp_oauth_cli import LoginFixture


@pytest.mark.anyio
@pytest.mark.parametrize("mode", ["paste", "both", "denied", "closed"])
async def test_manual_authorization_never_opens_browser_and_exchanges_at_most_once(mode):
    remote = OAuthService()
    presenter = Mock(spec=McpOAuthPresenter)
    opener = AsyncMock()
    reader = AsyncMock(spec=McpOAuthCallbackInput)
    addresses = []
    closed = asyncio.Event()

    async def read_callback(*, max_bytes):
        try:
            async with remote.client() as browser:
                response = await browser.get(presenter.authorization_url.call_args.args[0])
            url = httpx.URL(response.headers["Location"])
            addresses.append(str(url))
            if mode == "closed":
                raise McpOAuthError("callback_input_closed")
            if mode == "denied":
                url = url.copy_remove_param("code").copy_add_param("error", "access_denied")
            if mode == "both":
                await visit_callback(str(url))
            return str(url)
        finally:
            closed.set()

    reader.read_callback.side_effect = read_callback
    client = remote.client()
    adapter = McpOAuthAdapter(open_browser=opener, client_factory=lambda: client)
    request = McpOAuthLoginRequest(McpOAuthTarget("server", RESOURCE_URL))
    if mode in ("closed", "denied"):
        with pytest.raises(McpOAuthError) as error:
            await adapter.authorize(request, 7, presenter, callback_input=reader)
        assert error.value.code == ("callback_input_closed" if mode == "closed" else "authorization_denied")
        assert not remote.token_requests
    else:
        saved = await adapter.authorize(request, 7, presenter, callback_input=reader)
        assert saved.generation == 7 and saved.token is not None
        assert len(remote.token_requests) == 1
    opener.assert_not_called()
    assert client.is_closed and closed.is_set()
    await assert_callback_closed(addresses[0])


@pytest.mark.anyio
@pytest.mark.parametrize("ending", ["cancel", "timeout", "logout"])
async def test_manual_token_exchange_cancellation_timeout_and_logout_preserve_commit_boundary(tmp_path: Path, ending):
    remote = OAuthService()
    presenter = Mock(spec=McpOAuthPresenter)
    reader = AsyncMock(spec=McpOAuthCallbackInput)
    entered, release = asyncio.Event(), asyncio.Event()
    addresses = []

    async def read_callback(*, max_bytes):
        async with remote.client() as browser:
            response = await browser.get(presenter.authorization_url.call_args.args[0])
        value = response.headers["Location"]
        addresses.append(value)
        return value

    async def handle(request):
        if str(request.url) == TOKEN_URL:
            entered.set()
            await release.wait()
        return await remote.handle(request)

    reader.read_callback.side_effect = read_callback
    vault = MemoryVault()
    store = SystemMcpCredentialStore(config_root=tmp_path / "config", state_root=tmp_path / "state", vault=vault)
    client = httpx.AsyncClient(transport=httpx.MockTransport(handle))
    service = McpOAuthService(store, McpOAuthAdapter(open_browser=AsyncMock(), client_factory=lambda: client))
    target = McpOAuthTarget("server", RESOURCE_URL)
    request = McpOAuthLoginRequest(target, timeout_sec=1 if ending == "timeout" else 10)
    task = asyncio.create_task(service.login(request, presenter, callback_input=reader))
    try:
        await asyncio.wait_for(entered.wait(), 3)
        if ending == "cancel":
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        elif ending == "timeout":
            with pytest.raises(McpOAuthError) as error:
                await task
            assert error.value.code == "timeout"
        else:
            await service.logout(target)
            release.set()
            with pytest.raises(McpOAuthStorageError) as error:
                await task
            assert error.value.code == "credential_conflict"
        assert client.is_closed and not vault.values
        assert (await store.read(target)).snapshot is None
        await assert_callback_closed(addresses[0])
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


def test_cli_manual_selects_input_port_without_exposing_callback(tmp_path, capsys):
    fixture = LoginFixture(tmp_path)
    reader = AsyncMock(spec=McpOAuthCallbackInput)
    presenter = Mock(spec=McpOAuthPresenter)

    async def read_callback(*, max_bytes):
        async with fixture.remote.client() as browser:
            response = await browser.get(presenter.authorization_url.call_args.args[0])
        return response.headers["Location"]

    reader.read_callback.side_effect = read_callback
    with patch("frontends.cli.mcp_registry.HiddenOAuthCallbackInput", return_value=reader), patch(
        "frontends.cli.mcp_registry._OAuthPresenter", return_value=presenter,
    ):
        assert fixture.run("login", fixture.name, "--manual") == 0
    assert fixture.browser.authorization_url is None
    assert len(fixture.remote.token_requests) == 1
    text = capsys.readouterr()
    assert "Successfully logged in" in text.out and "code-1" not in text.out + text.err


def test_cli_manual_non_terminal_fails_before_network_discovery(tmp_path, capsys):
    fixture = LoginFixture(tmp_path)
    assert fixture.run("login", fixture.name, "--manual") == 1
    assert not fixture.remote.requests
    output = capsys.readouterr()
    assert "interactive terminal" in output.out + output.err
