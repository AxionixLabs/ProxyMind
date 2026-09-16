# -*- coding: utf-8 -*-

import asyncio

import anyio
import httpx
import pytest
from pathlib import Path
from unittest.mock import (
    AsyncMock,
    patch,
)
from mcp.shared.exceptions import McpError

from agent.application.mcp.oauth import McpOAuthService
from agent.ports.mcp_runtime import (
    McpRuntimeContext,
    McpServicesBusy,
)
from infrastructure.config.session import ConfigSession
from infrastructure.config.store import ConfigStore
from frontends.cli.entry import run
from infrastructure.mcp.external_group import ExternalMcpGroup
from infrastructure.mcp.external_runtime import ExternalMcpRuntime
from infrastructure.mcp.oauth_adapter import (
    McpOAuthAdapter,
    McpOAuthRefreshAdapter,
)
from infrastructure.mcp.settings import normalize_mcp_servers
from tests.infrastructure.mcp.oauth_browser import OAuthBrowser
from tests.infrastructure.mcp.oauth_fixture import (
    OAuthService,
    RESOURCE_URL,
)
from tests.infrastructure.mcp.oauth_runtime_fixture import RuntimeOAuthFixture


def configured(fixture: RuntimeOAuthFixture):
    return {fixture.target.config_key: {"url": RESOURCE_URL, "oauth": {"scopes": ["read"]}}}


def test_cli_login_credentials_are_used_by_production_runtime_with_cli_scope_override(tmp_path: Path) -> None:
    fixture = RuntimeOAuthFixture(tmp_path)
    remote = OAuthService()
    browser = OAuthBrowser(remote)
    config_path = tmp_path / "config" / "config.toml"
    config_store = ConfigStore(config_path)
    servers = {fixture.target.config_key: {"url": RESOURCE_URL, "oauth": {"scopes": ["configured-scope"]}}}
    config_store.update({("mcp_servers",): servers})
    service = McpOAuthService(fixture.store, McpOAuthAdapter(open_browser=browser.open, client_factory=remote.client))
    with patch("frontends.cli.mcp_registry.application_config_path", return_value=config_path):
        assert run(arguments=["mcp", "login", fixture.target.config_key, "--scopes", "read"], mcp_oauth_factory=lambda root: service) == 0

    async def use_runtime() -> None:
        with patch("httpx.AsyncHTTPTransport", side_effect=lambda **kwargs: httpx.MockTransport(remote.handle)), patch(
            "infrastructure.mcp.external_group.preflight_server", AsyncMock(),
        ):
            group = ExternalMcpGroup(credential_store=fixture.store)
            try:
                assert await group.start(normalize_mcp_servers(servers)) == 1
                assert not (await group.call_tool(next(iter(group.tools)), {})).isError
            finally:
                await group.close()
        assert remote.authorizations[0].scope == "read"
        assert len(remote.token_requests) == 1

    anyio.run(use_runtime)


@pytest.mark.anyio
async def test_production_owner_discovers_calls_refreshes_and_recovers_on_new_connection(tmp_path: Path) -> None:
    fixture = RuntimeOAuthFixture(tmp_path)
    await fixture.save(expired=True)
    servers = normalize_mcp_servers(configured(fixture))
    with patch("httpx.AsyncHTTPTransport", side_effect=lambda **kwargs: httpx.MockTransport(fixture.handle)), patch(
        "infrastructure.mcp.external_group.preflight_server", AsyncMock(),
    ), patch("infrastructure.mcp.oauth_runtime.McpOAuthRefreshAdapter", side_effect=lambda **kwargs: McpOAuthRefreshAdapter(client_factory=fixture.client)):
        for _ in range(2):
            group = ExternalMcpGroup(credential_store=fixture.store)
            try:
                assert await group.start(servers) == 1
                view = group.service_snapshots[0]
                assert view.state == "ready" and view.authorization_error is None
                assert len(group.tools) == 1
                result = await group.call_tool(next(iter(group.tools)), {})
                assert result.content[0].type == "text" and result.content[0].text == "fixture result"
            finally:
                await group.close()
            assert not group.owned_keys
    assert fixture.calls == 2 and len(fixture.refresh_requests) == 1


@pytest.mark.anyio
@pytest.mark.parametrize("failure", ["missing", "logout", "401", "403", "network", "redirect"])
async def test_runtime_authentication_failure_withdraws_directory_and_closes_owner(tmp_path: Path, failure: str) -> None:
    fixture = RuntimeOAuthFixture(tmp_path)
    if failure != "missing":
        await fixture.save()
    servers = normalize_mcp_servers(configured(fixture))
    with patch("httpx.AsyncHTTPTransport", side_effect=lambda **kwargs: httpx.MockTransport(fixture.handle)), patch(
        "infrastructure.mcp.external_group.preflight_server", AsyncMock(),
    ), patch("infrastructure.mcp.oauth_runtime.McpOAuthRefreshAdapter", side_effect=lambda **kwargs: McpOAuthRefreshAdapter(client_factory=fixture.client)):
        group = ExternalMcpGroup(credential_store=fixture.store)
        try:
            connected = await group.start(servers)
            if failure == "missing":
                assert connected == 0
            else:
                assert connected == 1
                name = next(iter(group.tools))
                before = len(fixture.requests)
                if failure == "logout":
                    await fixture.store.delete(fixture.target)
                elif failure in ("401", "403"):
                    fixture.resource_status = int(failure)
                elif failure == "redirect":
                    fixture.resource_status = 307
                else:
                    await fixture.save(expired=True)
                    fixture.refresh_mode = "network"
                with pytest.raises(McpError):
                    await asyncio.wait_for(group.call_tool(name, {}), 3)
                # Allow the existing owner completion callback to settle the snapshot.
                await asyncio.sleep(0)
                assert fixture.calls == 0
                if failure in ("401", "403", "redirect"):
                    assert len(fixture.requests) == before + 1
            snapshot = group.service_snapshots[0]
            assert snapshot.state == "failed" and snapshot.authorization_error is not None
            assert not group.tools
            assert "access-" not in repr(snapshot) and "refresh-" not in repr(snapshot)
        finally:
            await group.close()
        assert not group.owned_keys


@pytest.mark.anyio
async def test_authenticated_runtime_preserves_frozen_catalog_busy_and_retirement(tmp_path: Path) -> None:
    fixture = RuntimeOAuthFixture(tmp_path)
    await fixture.save()
    config_store = ConfigStore(tmp_path / "runtime.toml")
    config_store.update({("mcp_servers",): configured(fixture)})
    context = McpRuntimeContext(
        ConfigSession(config_store, workspace=tmp_path), AsyncMock(), AsyncMock(),
        lambda operation: operation,
    )
    with patch("httpx.AsyncHTTPTransport", side_effect=lambda **kwargs: httpx.MockTransport(fixture.handle)), patch(
        "infrastructure.mcp.external_group.preflight_server", AsyncMock(),
    ):
        runtime = ExternalMcpRuntime(context, credential_store=fixture.store)
        try:
            await runtime.start()
            with runtime.use_tools() as root, runtime.use_tools("raw-server") as hook:
                assert root is not None and hook is not None
                assert tuple(root.tools) == tuple(hook.tools)
                with pytest.raises(McpServicesBusy):
                    await runtime.stop_services()
                result = await root.call_tool(next(iter(root.tools)), {})
                assert not result.isError
                result = await hook.call_hook_tool("raw-server", "read_fixture", {})
                assert not result.isError
                runtime.retire()
                with runtime.use_tools() as retired:
                    assert retired is None
            await runtime.stop()
        finally:
            await runtime.stop()
        assert not runtime.started and fixture.calls == 2


@pytest.mark.anyio
async def test_owner_close_waits_for_inflight_refresh_and_reaps_request(tmp_path: Path) -> None:
    fixture = RuntimeOAuthFixture(tmp_path)
    await fixture.save()
    with patch("httpx.AsyncHTTPTransport", side_effect=lambda **kwargs: httpx.MockTransport(fixture.handle)), patch(
        "infrastructure.mcp.external_group.preflight_server", AsyncMock(),
    ), patch("infrastructure.mcp.oauth_runtime.McpOAuthRefreshAdapter", side_effect=lambda **kwargs: McpOAuthRefreshAdapter(client_factory=fixture.client)):
        group = ExternalMcpGroup(credential_store=fixture.store)
        call = None
        close = None
        try:
            assert await group.start(normalize_mcp_servers(configured(fixture))) == 1
            await fixture.save(expired=True)
            fixture.refresh_release.clear()
            call = asyncio.create_task(group.call_tool(next(iter(group.tools)), {}))
            await asyncio.wait_for(fixture.refresh_entered.wait(), 3)
            close = asyncio.create_task(group.close())
            await asyncio.sleep(0)
            fixture.refresh_release.set()
            await asyncio.wait_for(close, 4)
            await asyncio.gather(call, return_exceptions=True)
            saved = (await fixture.store.read(fixture.target)).snapshot
            assert saved is not None and saved.token is not None and saved.token.access_token == "access-2"
            assert not group.owned_keys and not group.tools
        finally:
            fixture.refresh_release.set()
            if call is not None and not call.done():
                call.cancel()
                await asyncio.gather(call, return_exceptions=True)
            if close is not None:
                await close
            await group.close()


@pytest.mark.anyio
@pytest.mark.parametrize("mode", ["anonymous", "header", "bearer_missing", "bearer_present", "env_header_missing"])
async def test_existing_http_authentication_selection_does_not_fall_back_to_oauth(tmp_path: Path, mode: str) -> None:
    fixture = RuntimeOAuthFixture(tmp_path)
    fixture.anonymous = True
    fixture.vault.fail_read = True
    settings = {"url": RESOURCE_URL}
    if mode == "header":
        settings["http_headers"] = {"authorization": "Basic explicit"}
    elif mode in ("bearer_missing", "bearer_present"):
        settings["bearer_token_env_var"] = "EXPLICIT_TOKEN"
    elif mode == "env_header_missing":
        settings["env_http_headers"] = {"Authorization": "EXPLICIT_TOKEN"}
    environment = {"EXPLICIT_TOKEN": "explicit-secret"} if mode == "bearer_present" else {}
    with patch(
        "httpx.AsyncHTTPTransport", side_effect=lambda **kwargs: httpx.MockTransport(fixture.handle),
    ), patch("infrastructure.mcp.external_group.preflight_server", AsyncMock()):
        group = ExternalMcpGroup(credential_store=fixture.store)
        try:
            assert await group.start(normalize_mcp_servers({fixture.target.config_key: settings}, environment=environment)) == 1
            assert not fixture.refresh_requests
            expected = "Basic explicit" if mode == "header" else "Bearer explicit-secret" if mode == "bearer_present" else None
            assert fixture.requests[0].headers.get("Authorization") == expected
        finally:
            await group.close()
