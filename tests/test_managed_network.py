# -*- coding: utf-8 -*-

import asyncio
import socket
from unittest.mock import AsyncMock
from unittest.mock import patch

import pytest

from agent.domain.approvals import NetworkProtocol, NetworkTarget
from infrastructure.platform.network import (
    BlockedNetworkRequest,
    ManagedNetworkProxy,
    ManagedNetworkRule,
    NetworkDecision,
    StaticNetworkPolicy,
    managed_network_backend_name,
)
from infrastructure.platform import network as network_module


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


def test_session_proxy_keeps_session_grants_isolated() -> None:
    target = NetworkTarget("api.example.test", NetworkProtocol.HTTPS, 443)
    policy = StaticNetworkPolicy()
    template = ManagedNetworkProxy(policy)
    first = template.for_session(session_id="session-1")
    second = template.for_session(session_id="session-2")

    policy.grant_for_session(target, first.session_id)

    assert policy.decide(target, session_id=first.session_id) is NetworkDecision.ALLOW
    assert policy.decide(target, session_id=second.session_id) is NetworkDecision.DENY


@pytest.mark.anyio
async def test_managed_proxy_rechecks_policy_after_blocked_callback() -> None:
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
    policy = StaticNetworkPolicy()
    session_proxy = ManagedNetworkProxy(policy).for_session(session_id="session-1")

    async def approve_once(request: BlockedNetworkRequest) -> None:
        policy.grant_once(request.target)

    session_proxy._on_blocked = approve_once
    await session_proxy.start()
    try:
        reader, writer = await asyncio.open_connection(
            session_proxy.host,
            session_proxy.port,
        )
        writer.write(
            f"GET http://127.0.0.1:{target_port}/approved HTTP/1.1\r\n"
            f"Host: 127.0.0.1:{target_port}\r\n\r\n".encode("ascii")
        )
        response = await reader.read()
        writer.close()
        await writer.wait_closed()
        assert response.startswith(b"HTTP/1.1 200 OK")
    finally:
        await session_proxy.close()
        target_server.close()
        await target_server.wait_closed()


@pytest.mark.anyio
async def test_managed_proxy_forwards_allowed_socks5_connect() -> None:
    async def target_handler(
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        payload = await reader.readexactly(4)
        writer.write(payload)
        await writer.drain()
        writer.close()

    target_server = await asyncio.start_server(target_handler, "127.0.0.1", 0)
    target_port = int(target_server.sockets[0].getsockname()[1])
    proxy = ManagedNetworkProxy(StaticNetworkPolicy((
        ManagedNetworkRule(
            host="127.0.0.1",
            protocol=NetworkProtocol.SOCKS5_TCP,
            port=target_port,
            decision=NetworkDecision.ALLOW,
        ),
    )))
    await proxy.start()
    try:
        reader, writer = await asyncio.open_connection(proxy.host, proxy.port)
        writer.write(b"\x05\x01\x00")
        await writer.drain()
        assert await reader.readexactly(2) == b"\x05\x00"
        writer.write(
            b"\x05\x01\x00\x01"
            b"\x7f\x00\x00\x01"
            + target_port.to_bytes(2, "big")
        )
        await writer.drain()
        assert (await reader.readexactly(10))[:2] == b"\x05\x00"
        writer.write(b"ping")
        await writer.drain()
        assert await reader.readexactly(4) == b"ping"
        writer.close()
        await writer.wait_closed()
    finally:
        await proxy.close()
        target_server.close()
        await target_server.wait_closed()


@pytest.mark.anyio
async def test_managed_proxy_rejects_socks5_udp_command() -> None:
    proxy = ManagedNetworkProxy(StaticNetworkPolicy())
    await proxy.start()
    try:
        reader, writer = await asyncio.open_connection(proxy.host, proxy.port)
        writer.write(b"\x05\x01\x00")
        await writer.drain()
        assert await reader.readexactly(2) == b"\x05\x00"
        writer.write(b"\x05\x03\x00\x01\x7f\x00\x00\x01\x00\x35")
        await writer.drain()
        assert (await reader.readexactly(10))[1] == 0x07
        writer.close()
        await writer.wait_closed()
    finally:
        await proxy.close()


@pytest.mark.anyio
async def test_dns_private_resolution_is_rejected_but_explicit_ip_is_allowed() -> None:
    resolver = AsyncMock(return_value=[(
        socket.AF_INET,
        socket.SOCK_STREAM,
        6,
        "",
        ("192.168.10.4", 0),
    )])
    loop = type("Resolver", (), {"getaddrinfo": resolver})()

    with patch.object(network_module.asyncio, "get_running_loop", return_value=loop):
        with pytest.raises(ValueError, match="local address"):
            await network_module._safe_upstream_host("internal.example.test")
        assert await network_module._safe_upstream_host("192.168.10.4") == "192.168.10.4"


@pytest.mark.anyio
@pytest.mark.parametrize("failure", [OSError("dns failed"), asyncio.TimeoutError()])
async def test_dns_resolution_failure_is_fail_closed(failure: BaseException) -> None:
    resolver = AsyncMock(side_effect=failure)
    loop = type("Resolver", (), {"getaddrinfo": resolver})()

    with patch.object(network_module.asyncio, "get_running_loop", return_value=loop):
        with pytest.raises(ValueError, match="hostname .*resolved|resolution timed out"):
            await network_module._safe_upstream_host("unavailable.example.test")


async def _record_blocked(
    blocked: list[BlockedNetworkRequest],
    request: BlockedNetworkRequest,
) -> None:
    blocked.append(request)
