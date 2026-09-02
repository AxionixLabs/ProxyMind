# -*- coding: utf-8 -*-

import asyncio

import pytest

from agent.domain.approvals import NetworkProtocol, NetworkTarget
from infrastructure.platform.network import (
    ManagedNetworkProxy,
    ManagedNetworkRule,
    NetworkDecision,
    StaticNetworkPolicy,
    managed_network_backend_name,
)


def test_managed_network_backend_names_cover_all_target_platforms() -> None:
    assert managed_network_backend_name("win32") == "windows-managed-proxy"
    assert managed_network_backend_name("darwin") == "macos-managed-proxy"
    assert managed_network_backend_name("linux") == "linux-managed-proxy"


def test_static_policy_is_subdomain_aware_and_deny_wins() -> None:
    policy = StaticNetworkPolicy((
        ManagedNetworkRule(
            host="example.com",
            protocol=NetworkProtocol.HTTPS,
            decision=NetworkDecision.ALLOW,
        ),
        ManagedNetworkRule(
            host="private.example.com",
            protocol=NetworkProtocol.HTTPS,
            decision=NetworkDecision.DENY,
        ),
    ))

    assert policy.decide(NetworkTarget(
        "api.example.com", NetworkProtocol.HTTPS, 443
    )) is NetworkDecision.ALLOW
    assert policy.decide(NetworkTarget(
        "private.example.com", NetworkProtocol.HTTPS, 443
    )) is NetworkDecision.DENY
    assert policy.decide(NetworkTarget(
        "example.net", NetworkProtocol.HTTPS, 443
    )) is NetworkDecision.DENY


@pytest.mark.anyio
async def test_managed_proxy_denies_by_default_and_reports_blocked_request() -> None:
    blocked = []
    proxy = ManagedNetworkProxy(
        StaticNetworkPolicy(),
        on_blocked=lambda request: _record_blocked(blocked, request),
    )
    await proxy.start()
    try:
        environment = proxy.environment({"PATH": "test"})
        assert environment["HTTP_PROXY"] == proxy.proxy_url
        assert environment["http_proxy"] == proxy.proxy_url
        assert environment["NO_PROXY"] == ""

        reader, writer = await asyncio.open_connection(proxy.host, proxy.port)
        writer.write(
            b"GET http://blocked.example.test/ HTTP/1.1\r\n"
            b"Host: blocked.example.test\r\n\r\n"
        )
        response = await reader.read()
        writer.close()
        await writer.wait_closed()

        assert response.startswith(b"HTTP/1.1 403")
        assert len(blocked) == 1
        assert blocked[0].target.host == "blocked.example.test"
    finally:
        await proxy.close()


@pytest.mark.anyio
async def test_managed_proxy_forwards_allowed_http_request() -> None:
    async def target_handler(
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        await reader.readuntil(b"\r\n\r\n")
        writer.write(
            b"HTTP/1.1 200 OK\r\n"
            b"Content-Length: 2\r\n"
            b"Connection: close\r\n\r\nOK"
        )
        await writer.drain()
        writer.close()

    target_server = await asyncio.start_server(target_handler, "127.0.0.1", 0)
    target_port = int(target_server.sockets[0].getsockname()[1])
    proxy = ManagedNetworkProxy(StaticNetworkPolicy((
        ManagedNetworkRule(
            host="127.0.0.1",
            protocol=NetworkProtocol.HTTP,
            port=target_port,
            decision=NetworkDecision.ALLOW,
        ),
    )))
    await proxy.start()
    try:
        reader, writer = await asyncio.open_connection(proxy.host, proxy.port)
        writer.write(
            f"GET http://127.0.0.1:{target_port}/health?ok=1 HTTP/1.1\r\n"
            f"Host: 127.0.0.1:{target_port}\r\n\r\n".encode("ascii")
        )
        response = await reader.read()
        writer.close()
        await writer.wait_closed()
        assert response.endswith(b"\r\n\r\nOK")
        assert response.startswith(b"HTTP/1.1 200 OK")
    finally:
        await proxy.close()
        target_server.close()
        await target_server.wait_closed()


async def _record_blocked(blocked: list[object], request: object) -> None:
    blocked.append(request)
