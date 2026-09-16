# -*- coding: utf-8 -*-

import asyncio

import pytest
from unittest.mock import AsyncMock

from mcp import ClientSession
from mcp.client.auth import (
    OAuthFlowError,
    OAuthRegistrationError,
    OAuthTokenError,
)
from mcp.client.streamable_http import streamable_http_client
from mcp.shared.auth import OAuthClientInformationFull
from pydantic import AnyUrl

from metadata import const
from tests.infrastructure.mcp.oauth_fixture import (
    AUTH_METADATA_URL,
    CALLBACK_URL,
    CLIENT_METADATA_URL,
    RESOURCE_METADATA_URL,
    RESOURCE_URL,
    TOKEN_URL,
    MemoryTokenStorage,
    OAuthService,
)


@pytest.mark.anyio
@pytest.mark.parametrize("registration", ["dynamic", "preregistered", "cimd"])
async def test_sdk_authorization_reaches_authenticated_mcp_discovery(registration: str) -> None:
    service = OAuthService()
    storage = MemoryTokenStorage()
    if registration == "preregistered":
        registered = OAuthClientInformationFull(
            client_id="preregistered-client", redirect_uris=[AnyUrl(CALLBACK_URL)],
            token_endpoint_auth_method="none",
        )
        await storage.set_client_info(registered)
        service.registered_clients["preregistered-client"] = registered
    service.cimd_supported = registration == "cimd"
    provider = service.provider(
        storage, client_metadata_url=CLIENT_METADATA_URL if registration == "cimd" else None,
    )

    async with service.client(provider) as client:
        async with streamable_http_client(RESOURCE_URL, http_client=client) as (read, write, _):
            async with ClientSession(read, write) as session:
                initialized = await session.initialize()
                tools = await session.list_tools()

    assert initialized.serverInfo.name == "oauth-fixture"
    assert [tool.name for tool in tools.tools] == ["read_fixture"]
    assert len(service.registrations) == (1 if registration == "dynamic" else 0)
    if registration == "dynamic":
        assert service.registrations[0].client_name == const.APP_DESC
    authorization, = service.authorizations
    assert authorization.scope == "project:read"
    assert authorization.redirect_uri == CALLBACK_URL
    assert authorization.client_id == {
        "dynamic": "dynamic-client", "preregistered": "preregistered-client", "cimd": CLIENT_METADATA_URL,
    }[registration]
    assert storage.tokens is not None
    assert storage.tokens.access_token == "access-1"
    assert storage.tokens.refresh_token == "refresh-1"
    assert len(storage.saved_tokens) == 1
    urls = [str(request.url) for request in service.requests]
    assert RESOURCE_METADATA_URL in urls and AUTH_METADATA_URL in urls and TOKEN_URL in urls
    assert all("Authorization" not in request.headers for request in service.requests if request.url.host != "mcp.example.test")


@pytest.mark.anyio
async def test_sdk_cimd_uses_dynamic_registration_when_not_advertised() -> None:
    service = OAuthService()
    async with service.client(service.provider(MemoryTokenStorage(), client_metadata_url=CLIENT_METADATA_URL)) as client:
        response = await client.get(RESOURCE_URL)
    assert response.status_code == 200
    assert len(service.registrations) == 1
    assert service.authorizations[0].client_id == "dynamic-client"


@pytest.mark.anyio
@pytest.mark.parametrize("returned_state", [None, "wrong-state"])
async def test_sdk_rejects_callback_state_before_token_exchange(returned_state: str | None) -> None:
    service = OAuthService()
    storage = MemoryTokenStorage()
    # 回调是公开构造契约；替换测试服务回调后重新构造，不改 SDK 内部上下文。
    service.callback = AsyncMock(return_value=("code-1", returned_state))
    provider = service.provider(storage)
    async with service.client(provider) as client:
        with pytest.raises(OAuthFlowError, match="State parameter mismatch"):
            await client.get(RESOURCE_URL)
    assert service.token_requests == []
    assert storage.tokens is None


@pytest.mark.anyio
@pytest.mark.parametrize("failure", ["registration", "exchange", "storage"])
async def test_sdk_login_failure_never_produces_saved_tokens(failure: str) -> None:
    service = OAuthService()
    storage = MemoryTokenStorage()
    expected_error: type[Exception]
    if failure == "registration":
        service.registration_status = 400
        expected_error = OAuthRegistrationError
    elif failure == "exchange":
        service.token_status = 400
        expected_error = OAuthTokenError
    else:
        storage.set_tokens = AsyncMock(side_effect=OSError("fixture credential store unavailable"))
        expected_error = OSError
    async with service.client(service.provider(storage)) as client:
        with pytest.raises(expected_error):
            await client.get(RESOURCE_URL)
    assert storage.tokens is None
    assert storage.saved_tokens == []


@pytest.mark.anyio
@pytest.mark.parametrize("error", [OAuthFlowError("user denied authorization"), TimeoutError("callback timed out")])
async def test_sdk_callback_failure_propagates_without_exchanging_tokens(error: Exception) -> None:
    service = OAuthService()
    storage = MemoryTokenStorage()
    service.callback = AsyncMock(side_effect=error)
    async with service.client(service.provider(storage)) as client:
        with pytest.raises(type(error), match=str(error)):
            await client.get(RESOURCE_URL)
    assert service.token_requests == []
    assert storage.saved_tokens == []


@pytest.mark.anyio
async def test_sdk_waiting_callback_propagates_owner_cancellation() -> None:
    service = OAuthService()
    storage = MemoryTokenStorage()
    entered = asyncio.Event()
    release = asyncio.Event()

    async def callback() -> tuple[str, str | None]:
        """标记已进入回调等待，由测试拥有者取消本次登录。"""
        entered.set()
        await release.wait()
        return "code-1", None

    service.callback = callback
    async with service.client(service.provider(storage)) as client:
        task = asyncio.create_task(client.get(RESOURCE_URL))
        try:
            async with asyncio.timeout(2):
                await entered.wait()
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
    assert service.token_requests == []
    assert storage.saved_tokens == []
