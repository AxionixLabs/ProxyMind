# -*- coding: utf-8 -*-

import asyncio

import httpx
import pytest
from dataclasses import replace
from unittest.mock import Mock

from mcp.shared.auth import OAuthClientMetadata
from pydantic import (
    AnyUrl,
    JsonValue,
    TypeAdapter,
)

from agent.domain.mcp_oauth import (
    McpMetadataClient,
    McpOAuthError,
    McpOAuthLoginRequest,
    McpOAuthTarget,
    McpRegisteredClient,
)
from agent.ports.mcp_oauth import McpOAuthPresenter
from infrastructure.mcp.oauth_adapter import McpOAuthAdapter
from tests.infrastructure.mcp.oauth_browser import (
    OAuthBrowser,
    assert_callback_closed,
)
from tests.infrastructure.mcp.oauth_fixture import (
    AUTH_METADATA_URL,
    CLIENT_METADATA_URL,
    ISSUER_URL,
    OAuthService,
    RESOURCE_METADATA_URL,
    RESOURCE_URL,
    TOKEN_URL,
)


REQUEST = McpOAuthLoginRequest(McpOAuthTarget(" raw server ", RESOURCE_URL))
DOCUMENT = TypeAdapter(dict[str, JsonValue])


@pytest.mark.anyio
@pytest.mark.parametrize("strategy", ["dynamic", "registered", "metadata"])
async def test_registration_pkce_resource_binding_and_http_cleanup(strategy: str) -> None:
    service = OAuthService()
    browser = OAuthBrowser(service)
    request = REQUEST
    if strategy == "registered":
        reservation = await asyncio.start_server(lambda _reader, writer: writer.close(), "127.0.0.1", 0)
        port = reservation.sockets[0].getsockname()[1]
        reservation.close()
        await reservation.wait_closed()
        service.registered_clients["public-client"] = OAuthClientMetadata(
            redirect_uris=[AnyUrl(f"http://127.0.0.1:{port}/callback")], token_endpoint_auth_method="none",
        )
        request = replace(request, registration=McpRegisteredClient("public-client"), callback_port=port)
    elif strategy == "metadata":
        service.cimd_supported = True
        request = replace(request, registration=McpMetadataClient(CLIENT_METADATA_URL))
    client = service.client()
    presenter = Mock(spec=McpOAuthPresenter)

    async def open_browser(url: str) -> bool:
        presenter.authorization_url.assert_called_once_with(url)
        return await browser.open(url)

    snapshot = await McpOAuthAdapter(open_browser=open_browser, client_factory=lambda: client, clock=lambda: 1000).authorize(request, 7, presenter)
    assert snapshot.generation == 7 and snapshot.target == REQUEST.target
    assert snapshot.issuer == ISSUER_URL and snapshot.resource == RESOURCE_URL
    assert snapshot.token_endpoint == TOKEN_URL and snapshot.client.registration == strategy
    assert snapshot.token is not None and snapshot.token.expires_at == 4600
    assert len(service.registrations) == (1 if strategy == "dynamic" else 0)
    assert client.is_closed
    await assert_callback_closed(browser.callback_url)


@pytest.mark.anyio
@pytest.mark.parametrize(("explicit", "challenge", "resource", "server", "expected"), [
    (("chosen",), None, ["resource"], ["server"], ("chosen",)),
    (None, "required", ["resource"], ["server"], ("required",)),
    (None, None, ["resource"], ["server"], ("resource",)),
    (None, None, None, ["server"], ("server",)),
    ((), None, ["resource"], ["server"], ()),
    (None, None, [], ["server"], ()),
    (None, None, None, None, None),
])
async def test_scope_precedence_and_explicit_empty(
    explicit: tuple[str, ...] | None, challenge: str | None,
    resource: list[str] | None, server: list[str] | None, expected: tuple[str, ...] | None,
) -> None:
    service = OAuthService()
    service.challenge_scope, service.resource_scopes, service.server_scopes = challenge, resource, server
    browser = OAuthBrowser(service)
    snapshot = await McpOAuthAdapter(open_browser=browser.open, client_factory=service.client).authorize(
        replace(REQUEST, scopes=explicit), 0, Mock(spec=McpOAuthPresenter),
    )
    assert snapshot.token is not None and snapshot.token.granted_scopes == expected
    assert service.authorizations[0].scope == (" ".join(expected) if expected else None)


@pytest.mark.anyio
async def test_explicit_scope_cannot_silently_expand_for_challenge() -> None:
    service = OAuthService()
    service.challenge_scope = "admin"
    browser = OAuthBrowser(service)
    with pytest.raises(McpOAuthError) as error:
        await McpOAuthAdapter(open_browser=browser.open, client_factory=service.client).authorize(
            replace(REQUEST, scopes=()), 0, Mock(spec=McpOAuthPresenter),
        )
    assert error.value.code == "configuration_conflict"
    assert not service.authorizations and not service.registrations


@pytest.mark.anyio
async def test_config_headers_only_reach_mcp_origin() -> None:
    service = OAuthService()
    browser = OAuthBrowser(service)
    await McpOAuthAdapter(open_browser=browser.open, client_factory=service.client).authorize(
        replace(REQUEST, headers=(("X-Private", "private-value"),)), 0, Mock(spec=McpOAuthPresenter),
    )
    assert service.requests[0].headers["X-Private"] == "private-value"
    assert all("X-Private" not in request.headers for request in service.requests[1:])


@pytest.mark.anyio
@pytest.mark.parametrize(("endpoint", "change"), [
    (RESOURCE_METADATA_URL, {"resource": "https://another.example/mcp"}),
    (AUTH_METADATA_URL, {"issuer": "https://another.example/issuer"}),
    (AUTH_METADATA_URL, {"token_endpoint": "http://auth.example.test/token"}),
    (AUTH_METADATA_URL, {"registration_endpoint": "https://127.0.0.1/register"}),
    (AUTH_METADATA_URL, {"code_challenge_methods_supported": ["plain"]}),
    (AUTH_METADATA_URL, {"token_endpoint_auth_methods_supported": ["client_secret_basic"]}),
    (AUTH_METADATA_URL, {"registration_endpoint": None}),
    (AUTH_METADATA_URL, {"authorization_response_iss_parameter_supported": True}),
    (AUTH_METADATA_URL, {"authorization_endpoint": ISSUER_URL + "/authorize?scope=admin"}),
    (ISSUER_URL + "/register", {"client_secret": "must-not-be-stored"}),
    (TOKEN_URL, {"scope": "project:read admin"}),
    (TOKEN_URL, {"scope": ""}),
    (TOKEN_URL, {"expires_in": -1}),
    (TOKEN_URL, {"expires_in": True}),
    (TOKEN_URL, {"access_token": ""}),
    (TOKEN_URL, {"token_type": "Basic"}),
])
async def test_invalid_remote_contract_is_rejected_and_resources_closed(endpoint: str, change: dict[str, JsonValue]) -> None:
    service = OAuthService()
    browser = OAuthBrowser(service)

    async def handle(request: httpx.Request) -> httpx.Response:
        response = await service.handle(request)
        if str(request.url) == endpoint:
            document = DOCUMENT.validate_json(response.content)
            document.update(change)
            return httpx.Response(response.status_code, json=document)
        return response

    # The issuer-required case deliberately receives a response without iss.
    if change.get("authorization_response_iss_parameter_supported") is True:
        browser.mode = "missing_code"
    client = httpx.AsyncClient(transport=httpx.MockTransport(handle))
    with pytest.raises(McpOAuthError):
        await McpOAuthAdapter(open_browser=browser.open, client_factory=lambda: client).authorize(
            REQUEST, 0, Mock(spec=McpOAuthPresenter),
        )
    assert client.is_closed
    await assert_callback_closed(browser.callback_url)


@pytest.mark.anyio
@pytest.mark.parametrize("failure", ["unavailable", "redirect", "malformed", "oversize", "network", "timeout"])
async def test_discovery_failure_does_not_open_browser_or_leak_payload(failure: str) -> None:
    service = OAuthService()
    browser = Mock(spec=McpOAuthPresenter)
    opener = Mock()

    async def handle(request: httpx.Request) -> httpx.Response:
        if str(request.url) == RESOURCE_URL:
            return await service.handle(request)
        if failure == "network":
            raise httpx.ConnectError("private-value", request=request)
        if failure == "timeout":
            raise httpx.ReadTimeout("private-value", request=request)
        if failure == "unavailable":
            return httpx.Response(404)
        if failure == "redirect":
            return httpx.Response(302, headers={"Location": "https://private-value.example"})
        return httpx.Response(200, content=b"private-value" if failure == "malformed" else b" " * (1024 * 1024 + 1))

    client = httpx.AsyncClient(transport=httpx.MockTransport(handle))
    with pytest.raises(McpOAuthError) as error:
        await McpOAuthAdapter(open_browser=opener, client_factory=lambda: client).authorize(REQUEST, 0, browser)
    assert "private-value" not in str(error.value)
    opener.assert_not_called()
    assert client.is_closed


@pytest.mark.anyio
async def test_path_resource_and_oidc_discovery_fallback_order() -> None:
    service = OAuthService()
    browser = OAuthBrowser(service)
    requests: list[str] = []
    resource_root = "https://mcp.example.test/.well-known/oauth-protected-resource"
    oidc_path = "https://auth.example.test/.well-known/openid-configuration/tenant"

    async def handle(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        requests.append(url)
        if url == RESOURCE_URL:
            return httpx.Response(401)
        if url in (RESOURCE_METADATA_URL, AUTH_METADATA_URL):
            return httpx.Response(404)
        if url == resource_root:
            return await service.handle(httpx.Request("GET", RESOURCE_METADATA_URL))
        if url == oidc_path:
            return await service.handle(httpx.Request("GET", AUTH_METADATA_URL))
        return await service.handle(request)

    await McpOAuthAdapter(open_browser=browser.open, client_factory=lambda: httpx.AsyncClient(transport=httpx.MockTransport(handle))).authorize(REQUEST, 0, Mock(spec=McpOAuthPresenter))
    assert requests[:5] == [RESOURCE_URL, RESOURCE_METADATA_URL, resource_root, AUTH_METADATA_URL, oidc_path]
