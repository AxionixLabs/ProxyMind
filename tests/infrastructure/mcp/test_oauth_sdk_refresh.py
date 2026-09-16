# -*- coding: utf-8 -*-

import pytest
from unittest.mock import (
    AsyncMock,
    patch,
)

from mcp.client.auth import OAuthFlowError
from mcp.shared.auth import OAuthToken

from tests.infrastructure.mcp.oauth_fixture import (
    RESOURCE_URL,
    TOKEN_URL,
    MemoryTokenStorage,
    OAuthService,
)


@pytest.mark.anyio
async def test_sdk_live_provider_refreshes_rotating_tokens_at_discovered_endpoint() -> None:
    service = OAuthService()
    service.expires_in = 1
    storage = MemoryTokenStorage()
    # SDK 没有时钟注入参数；控制标准库时钟避免依赖操作系统时间精度或真实等待。
    with patch("time.time", return_value=1000.0) as clock:
        async with service.client(service.provider(storage)) as client:
            first = await client.get(RESOURCE_URL)
            clock.return_value = 1002.0
            second = await client.get(RESOURCE_URL)
            clock.return_value = 1004.0
            third = await client.get(RESOURCE_URL)
    assert [first.status_code, second.status_code, third.status_code] == [200, 200, 200]
    assert len(service.authorizations) == 1
    assert [(item.grant_type, item.refresh_token) for item in service.token_requests] == [
        ("authorization_code", None), ("refresh_token", "refresh-1"), ("refresh_token", "refresh-2"),
    ]
    assert [token.refresh_token for token in storage.saved_tokens] == ["refresh-1", "refresh-2", "refresh-3"]
    assert [str(request.url) for request in service.requests if request.method == "POST" and "token" in request.url.path] == [TOKEN_URL] * 3


@pytest.mark.anyio
async def test_sdk_cold_start_sends_expired_token_without_restoring_expiry_or_metadata() -> None:
    service = OAuthService()
    service.expires_in = 1
    storage = MemoryTokenStorage()
    with patch("time.time", return_value=1000.0) as clock:
        async with service.client(service.provider(storage)) as client:
            await client.get(RESOURCE_URL)
        assert storage.tokens is not None
        service.revoked_access_tokens.add(storage.tokens.access_token)
        before_restart = len(service.requests)
        service.authorize = AsyncMock(side_effect=OAuthFlowError("interactive login required"))
        clock.return_value = 1002.0

        async with service.client(service.provider(storage)) as restarted:
            with pytest.raises(OAuthFlowError, match="interactive login required"):
                await restarted.get(RESOURCE_URL)

    first_after_restart = service.requests[before_restart]
    assert str(first_after_restart.url) == RESOURCE_URL
    assert first_after_restart.headers["Authorization"] == "Bearer access-1"
    assert [request.grant_type for request in service.token_requests] == ["authorization_code"]
    service.authorize.assert_awaited_once()


@pytest.mark.anyio
async def test_sdk_401_with_unexpired_token_reauthorizes_without_refresh() -> None:
    service = OAuthService()
    storage = MemoryTokenStorage()
    async with service.client(service.provider(storage)) as client:
        await client.get(RESOURCE_URL)
        service.revoked_access_tokens.add("access-1")
        response = await client.get(RESOURCE_URL)
    assert response.status_code == 200
    assert len(service.authorizations) == 2
    assert [request.grant_type for request in service.token_requests] == ["authorization_code"] * 2


@pytest.mark.anyio
async def test_sdk_rejected_refresh_keeps_old_storage_until_interactive_login_succeeds() -> None:
    service = OAuthService()
    service.expires_in = 1
    storage = MemoryTokenStorage()
    with patch("time.time", return_value=1000.0) as clock:
        async with service.client(service.provider(storage)) as client:
            await client.get(RESOURCE_URL)
            service.refresh_status = 400
            service.revoked_access_tokens.add("access-1")
            clock.return_value = 1002.0
            response = await client.get(RESOURCE_URL)
    assert response.status_code == 200
    assert [request.grant_type for request in service.token_requests] == [
        "authorization_code", "refresh_token", "authorization_code",
    ]
    assert [token.refresh_token for token in storage.saved_tokens] == ["refresh-1", "refresh-2"]
    assert len(service.authorizations) == 2


@pytest.mark.anyio
async def test_sdk_does_not_reread_external_credential_updates_before_refresh() -> None:
    service = OAuthService()
    service.expires_in = 1
    storage = MemoryTokenStorage()
    with patch("time.time", return_value=1000.0) as clock:
        async with service.client(service.provider(storage)) as client:
            await client.get(RESOURCE_URL)
            await storage.set_tokens(OAuthToken(
                access_token="external-access", refresh_token="external-refresh", expires_in=3600,
            ))
            service.access_token = "external-access"
            service.refresh_token = "external-refresh"
            clock.return_value = 1002.0
            response = await client.get(RESOURCE_URL)
    assert response.status_code == 200
    assert service.token_requests[1].grant_type == "refresh_token"
    assert service.token_requests[1].refresh_token == "refresh-1"
    assert len(service.authorizations) == 2
    assert [token.refresh_token for token in storage.saved_tokens] == [
        "refresh-1", "external-refresh", "refresh-2",
    ]
