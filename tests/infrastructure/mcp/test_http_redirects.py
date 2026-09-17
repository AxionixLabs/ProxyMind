# -*- coding: utf-8 -*-

import asyncio
from unittest.mock import patch

import httpx
import pytest

from infrastructure.mcp.transport import external_http_client


@pytest.mark.anyio
@pytest.mark.parametrize("location", [
    "https://other.example/mcp?secret=redirect-value",
    "https://origin.example:444/mcp",
    "http://origin.example/mcp",
    "https://user:redirect-value@origin.example/mcp",
    "https://origin.example:invalid/mcp?secret=redirect-value",
])
async def test_redirect_rejected_before_headers_or_body_reach_target(location: str) -> None:
    requests: list[httpx.Request] = []
    responses: list[httpx.Response] = []

    def handle(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        response = httpx.Response(307, headers={"location": location})
        responses.append(response)
        return response

    with patch("infrastructure.mcp.transport.httpx.AsyncHTTPTransport", return_value=httpx.MockTransport(handle)):
        async with external_http_client(
            headers={"X-Api-Key": "synthetic-secret"}, disconnected=asyncio.Event(),
        ) as client:
            with pytest.raises(httpx.RemoteProtocolError) as error:
                await client.post("https://origin.example/mcp", json={"arguments": "synthetic-body"})
    assert len(requests) == 1
    assert responses[0].is_closed
    assert "redirect-value" not in str(error.value)
    assert "synthetic" not in str(error.value)


@pytest.mark.anyio
async def test_second_redirect_cannot_escape_origin() -> None:
    requests: list[httpx.Request] = []

    def handle(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        location = "/second" if len(requests) == 1 else "https://other.example/third"
        return httpx.Response(307, headers={"location": location})

    with patch("infrastructure.mcp.transport.httpx.AsyncHTTPTransport", return_value=httpx.MockTransport(handle)):
        async with external_http_client(disconnected=asyncio.Event()) as client:
            with pytest.raises(httpx.RemoteProtocolError, match="different origin"):
                await client.get("https://origin.example/first")
    assert [request.url.path for request in requests] == ["/first", "/second"]
    assert all(request.url.host == "origin.example" for request in requests)


@pytest.mark.anyio
@pytest.mark.parametrize("status, method", [(301, "GET"), (302, "GET"), (303, "GET"), (307, "POST"), (308, "POST")])
async def test_same_origin_redirect_preserves_http_semantics(status: int, method: str) -> None:
    requests: list[httpx.Request] = []
    responses: list[httpx.Response] = []

    def handle(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        response = httpx.Response(status, headers={"location": "/next"}) if len(requests) == 1 else httpx.Response(200)
        responses.append(response)
        return response

    with patch("infrastructure.mcp.transport.httpx.AsyncHTTPTransport", return_value=httpx.MockTransport(handle)):
        async with external_http_client(
            headers={"X-Api-Key": "synthetic-secret"}, disconnected=asyncio.Event(),
        ) as client:
            await client.post("https://origin.example/start", content=b"synthetic-body")
    assert len(requests) == 2 and all(response.is_closed for response in responses)
    assert requests[1].method == method
    assert requests[1].content == (b"synthetic-body" if method == "POST" else b"")
    assert requests[1].headers["x-api-key"] == "synthetic-secret"


@pytest.mark.anyio
async def test_redirect_loop_has_bounded_requests_and_closes_responses() -> None:
    responses: list[httpx.Response] = []

    def handle(request: httpx.Request) -> httpx.Response:
        response = httpx.Response(307, headers={"location": "/loop"})
        responses.append(response)
        return response

    with patch("infrastructure.mcp.transport.httpx.AsyncHTTPTransport", return_value=httpx.MockTransport(handle)):
        async with external_http_client(disconnected=asyncio.Event()) as client:
            with pytest.raises(httpx.TooManyRedirects):
                await client.get("https://origin.example/loop")
    assert len(responses) == 11 and all(response.is_closed for response in responses)


@pytest.mark.anyio
@pytest.mark.parametrize("url, allowed", [
    ("http://localhost:9010/start", True),
    ("http://127.0.0.1:9010/start", True),
    ("http://[::1]:9010/start", True),
    ("http://plain.example/start", False),
])
async def test_plain_http_redirect_does_not_repeat_dns_hostname_requests(url: str, allowed: bool) -> None:
    requests: list[httpx.Request] = []

    def handle(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(302, headers={"location": "/next"}) if len(requests) == 1 else httpx.Response(200)

    with patch("infrastructure.mcp.transport.httpx.AsyncHTTPTransport", return_value=httpx.MockTransport(handle)):
        async with external_http_client(disconnected=asyncio.Event()) as client:
            if allowed:
                assert (await client.get(url)).status_code == 200
            else:
                with pytest.raises(httpx.RemoteProtocolError, match="require HTTPS"):
                    await client.get(url)
    assert len(requests) == (2 if allowed else 1)


@pytest.mark.anyio
async def test_oauth_client_does_not_follow_even_same_origin_redirect() -> None:
    requests: list[httpx.Request] = []

    def handle(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(307, headers={"location": "/next"})

    with patch("infrastructure.mcp.transport.httpx.AsyncHTTPTransport", return_value=httpx.MockTransport(handle)):
        async with external_http_client(auth=httpx.Auth(), disconnected=asyncio.Event()) as client:
            assert (await client.get("https://origin.example/start")).status_code == 307
    assert len(requests) == 1
