# -*- coding: utf-8 -*-

import httpx
import pytest
from dataclasses import replace
from pathlib import Path

from pydantic import (
    JsonValue,
    TypeAdapter,
)

from agent.domain.mcp_oauth import McpOAuthError
from infrastructure.mcp.oauth_adapter import McpOAuthRefreshAdapter
from tests.infrastructure.mcp.oauth_runtime_fixture import RuntimeOAuthFixture


@pytest.mark.anyio
@pytest.mark.parametrize("expires", [None, 42])
async def test_refresh_uses_bound_endpoint_and_closes_http_client_without_inventing_lifetime(tmp_path: Path, expires: int | None) -> None:
    fixture = RuntimeOAuthFixture(tmp_path)
    snapshot = await fixture.save(expired=True)

    async def handle(request: httpx.Request) -> httpx.Response:
        assert "Authorization" not in request.headers and "X-Private" not in request.headers
        assert str(request.url) == snapshot.token_endpoint
        response = await fixture.handle(request)
        document = TypeAdapter(dict[str, JsonValue]).validate_json(response.content)
        document["expires_in"] = expires
        del document["scope"]
        return httpx.Response(200, json=document)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handle))
    token = await McpOAuthRefreshAdapter(client_factory=lambda: client, clock=lambda: 1000).refresh(snapshot)
    assert token.expires_at == (None if expires is None else 1042)
    assert token.granted_scopes == ("read",) and client.is_closed


@pytest.mark.anyio
@pytest.mark.parametrize("endpoint", ["http://issuer.example/token", "https://127.0.0.1/token"])
async def test_refresh_refuses_unsafe_endpoint_before_sending_credentials(tmp_path: Path, endpoint: str) -> None:
    fixture = RuntimeOAuthFixture(tmp_path)
    snapshot = await fixture.save(expired=True)
    with pytest.raises(McpOAuthError):
        await McpOAuthRefreshAdapter(client_factory=fixture.client).refresh(replace(snapshot, token_endpoint=endpoint))
    assert not fixture.requests
