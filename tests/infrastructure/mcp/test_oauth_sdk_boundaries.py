# -*- coding: utf-8 -*-

import pytest

from tests.infrastructure.mcp.oauth_fixture import (
    RESOURCE_URL,
    MemoryTokenStorage,
    OAuthService,
)


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("challenge_scope", "resource_scopes", "server_scopes", "expected_scope"),
    [
        ("challenge:read", ["project:read"], ["org:read"], "challenge:read"),
        (None, ["project:read"], ["org:read"], "project:read"),
        (None, None, ["org:read"], "org:read"),
        (None, None, None, None),
    ],
)
async def test_sdk_discovery_overwrites_explicit_scope(
    challenge_scope: str | None, resource_scopes: list[str] | None,
    server_scopes: list[str] | None, expected_scope: str | None,
) -> None:
    service = OAuthService()
    service.challenge_scope = challenge_scope
    service.resource_scopes = resource_scopes
    service.server_scopes = server_scopes
    async with service.client(service.provider(MemoryTokenStorage(), explicit_scope="explicit:read")) as client:
        response = await client.get(RESOURCE_URL)
    assert response.status_code == 200
    assert service.authorizations[0].scope == expected_scope
    assert service.registrations[0].scope == expected_scope


@pytest.mark.anyio
async def test_sdk_403_insufficient_scope_starts_another_interactive_authorization() -> None:
    service = OAuthService()
    async with service.client(service.provider(MemoryTokenStorage())) as client:
        await client.get(RESOURCE_URL)
        service.denied_status = 403
        service.denied_challenge = 'Bearer error="insufficient_scope", scope="project:write"'
        response = await client.get(RESOURCE_URL)
    assert response.status_code == 403
    assert [request.scope for request in service.authorizations] == ["project:read", "project:write"]
    assert len(service.token_requests) == 2


@pytest.mark.anyio
async def test_sdk_403_without_scope_challenge_still_replays_the_request_once() -> None:
    service = OAuthService()
    async with service.client(service.provider(MemoryTokenStorage())) as client:
        await client.get(RESOURCE_URL)
        service.denied_status = 403
        before_denied = len(service.requests)
        response = await client.post(RESOURCE_URL, json={
            "jsonrpc": "2.0", "id": 1, "method": "tools/call",
            "params": {"name": "fixture_action", "arguments": {}},
        })
    assert response.status_code == 403
    assert len(service.requests[before_denied:]) == 2
    assert all(str(request.url) == RESOURCE_URL for request in service.requests[before_denied:])
    assert all(request.method == "POST" for request in service.requests[before_denied:])
    assert service.requests[-1].content == service.requests[-2].content
    assert len(service.authorizations) == 1


@pytest.mark.anyio
async def test_sdk_does_not_reject_discovered_metadata_with_different_issuer() -> None:
    service = OAuthService()
    service.metadata_issuer = "https://unrelated.example.test/issuer"
    async with service.client(service.provider(MemoryTokenStorage())) as client:
        response = await client.get(RESOURCE_URL)
    assert response.status_code == 200
    assert len(service.authorizations) == 1
    assert len(service.token_requests) == 1
