# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import asyncio
import enum
import ipaddress
import os
import socket
import sys
from collections.abc import (
    Mapping,
    Sequence,
)
from dataclasses import dataclass
from urllib.parse import urlsplit

from agent.domain.approvals import (
    NetworkProtocol,
    NetworkTarget,
)
from agent.ports.network import (
    NetworkBlockedHandler,
    NetworkBlockedHandlerFactory,
    NetworkPolicyPort,
)

__all__ = (
    "BlockedNetworkRequest",
    "ManagedNetworkProxy",
    "ManagedNetworkRule",
    "NetworkBlockedHandler",
    "NetworkBlockedHandlerFactory",
    "NetworkDecision",
    "StaticNetworkPolicy",
    "managed_network_backend_name",
)


class NetworkDecision(enum.StrEnum):
    """表示静态受管网络规则的允许或拒绝结果。"""

    ALLOW = "allow"
    DENY = "deny"


@dataclass(frozen=True, slots=True)
class ManagedNetworkRule:
    """保存一个按主机、协议和可选端口匹配的网络规则。"""

    host: str
    protocol: NetworkProtocol
    decision: NetworkDecision
    port: int | None = None

    def __post_init__(self) -> None:
        """校验规则字段，避免运行时出现开放式默认值。"""
        if not isinstance(self.host, str) or not self.host.strip():
            raise ValueError("network rule host is required")
        if not isinstance(self.protocol, NetworkProtocol):
            raise ValueError("network rule protocol is invalid")
        if not isinstance(self.decision, NetworkDecision):
            raise ValueError("network rule decision is invalid")
        if self.port is not None and (
            isinstance(self.port, bool)
            or not isinstance(self.port, int)
            or not 1 <= self.port <= 65535
        ):
            raise ValueError("network rule port is invalid")

    def matches(self, target: NetworkTarget) -> bool:
        """判断规则是否匹配网络目标。"""
        if target.protocol is not self.protocol:
            return False
        if self.port is not None and target.port != self.port:
            return False
        expected = self.host.strip().casefold().rstrip(".")
        candidate = target.host.strip().casefold().rstrip(".")
        if expected.startswith("*."):
            expected = expected[2:]
        return candidate == expected or candidate.endswith("." + expected)


@dataclass(frozen=True, slots=True)
class BlockedNetworkRequest:
    """保存一次被静态策略拒绝的网络目标。"""

    target: NetworkTarget
    reason: str


class _UnsafeNetworkTarget(ValueError):
    """表示域名解析到了不应由受管代理转发的本地地址。"""


class StaticNetworkPolicy:
    """按 deny-wins 规则评估网络目标，未匹配时默认拒绝。"""

    def __init__(self, rules: Sequence[ManagedNetworkRule] = ()) -> None:
        """绑定不可变静态网络规则。"""
        self._rules = tuple(rules)
        self._once_grants: dict[tuple[str, NetworkProtocol, int], int] = {}
        self._session_grants: dict[
            str,
            set[tuple[str, NetworkProtocol, int]],
        ] = {}

    @property
    def rules(self) -> tuple[ManagedNetworkRule, ...]:
        """返回规则快照。"""
        return self._rules

    def decide(
        self,
        target: NetworkTarget,
        *,
        session_id: str = "",
    ) -> NetworkDecision:
        """返回目标的静态决定，未命中时 fail closed。"""
        key = _target_key(target)
        if self._once_grants.get(key, 0):
            remaining = self._once_grants[key] - 1
            if remaining:
                self._once_grants[key] = remaining
            else:
                del self._once_grants[key]
            return NetworkDecision.ALLOW
        normalized_session = session_id.strip()
        if normalized_session and key in self._session_grants.get(
            normalized_session,
            set(),
        ):
            return NetworkDecision.ALLOW
        matches = tuple(rule for rule in self._rules if rule.matches(target))
        if any(rule.decision is NetworkDecision.DENY for rule in matches):
            return NetworkDecision.DENY
        if any(rule.decision is NetworkDecision.ALLOW for rule in matches):
            return NetworkDecision.ALLOW
        return NetworkDecision.DENY

    def grant_once(self, target: NetworkTarget) -> None:
        """授予目标一次网络连接。"""
        key = _target_key(target)
        self._once_grants[key] = self._once_grants.get(key, 0) + 1

    def grant_for_session(self, target: NetworkTarget, session_id: str) -> None:
        """授予目标在指定代理 Session 内的连接。"""
        normalized = session_id.strip()
        if not normalized:
            raise ValueError("session_id is required for session grant")
        self._session_grants.setdefault(normalized, set()).add(_target_key(target))

    def add_persistent_rule(self, target: NetworkTarget) -> ManagedNetworkRule:
        """添加允许目标主机和协议的持久运行时规则。"""
        rule = ManagedNetworkRule(
            host=target.host,
            protocol=target.protocol,
            decision=NetworkDecision.ALLOW,
            port=target.port,
        )
        self.install_rule(rule)
        return rule

    def install_rule(self, rule: ManagedNetworkRule) -> None:
        """安装一条已经由外部持久化确认的规则。"""
        if rule.decision is not NetworkDecision.ALLOW:
            raise ValueError("only allow rules can be installed at runtime")
        self._rules = (*self._rules, rule)

    def clear_session(self, session_id: str) -> None:
        """清除指定代理 Session 的全部临时授权。"""
        normalized = session_id.strip()
        if normalized:
            self._session_grants.pop(normalized, None)


def managed_network_backend_name(platform: str | None = None) -> str:
    """返回三平台统一网络代理后端标识。"""
    value = (sys.platform if platform is None else platform).strip().casefold()
    return {
        "win32": "windows-managed-proxy",
        "darwin": "macos-managed-proxy",
        "linux": "linux-managed-proxy",
    }.get(value, f"{value or 'unsupported'}-managed-proxy")


class ManagedNetworkProxy:
    """提供本机回环 HTTP、CONNECT 和 SOCKS5 CONNECT 代理。"""

    def __init__(
        self,
        policy: NetworkPolicyPort,
        *,
        host: str = "127.0.0.1",
        session_id: str = "",
        on_blocked: NetworkBlockedHandler | None = None,
    ) -> None:
        """绑定静态策略和可选的阻断观察回调。"""
        if not host.strip():
            raise ValueError("network proxy host is required")
        self.policy = policy
        self.host = host.strip()
        self.session_id = session_id.strip()
        self._on_blocked = on_blocked
        self._server: asyncio.AbstractServer | None = None
        self._connections: set[asyncio.Task[None]] = set()

    def for_session(
        self,
        *,
        session_id: str,
        on_blocked: NetworkBlockedHandler | None = None,
    ) -> "ManagedNetworkProxy":
        """创建共享策略下绑定单个进程 Session 的代理实例。"""
        return ManagedNetworkProxy(
            self.policy,
            host=self.host,
            session_id=str(session_id or "").strip(),
            on_blocked=self._on_blocked if on_blocked is None else on_blocked,
        )

    @property
    def running(self) -> bool:
        """返回代理监听器是否已经启动。"""
        return self._server is not None

    @property
    def port(self) -> int:
        """返回代理监听端口。"""
        server = self._server
        if server is None or not server.sockets:
            raise RuntimeError("network proxy is not running")
        address = server.sockets[0].getsockname()
        return int(address[1])

    @property
    def proxy_url(self) -> str:
        """返回供受管进程使用的回环代理 URL。"""
        return f"http://{self.host}:{self.port}"

    async def start(self) -> None:
        """启动回环代理并随机分配端口。"""
        if self._server is not None:
            return None
        self._server = await asyncio.start_server(
            self._accept,
            host=self.host,
            port=0,
            limit=65536,
        )

    def environment(
        self,
        base: Mapping[str, str] | None = None,
    ) -> dict[str, str]:
        """为受管进程生成不能通过大小写绕过的代理环境。"""
        if self._server is None:
            raise RuntimeError("network proxy is not running")
        environment = {
            str(key): str(value)
            for key, value in (base or os.environ).items()
        }
        proxy = self.proxy_url
        for name in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY"):
            environment[name] = proxy
            environment[name.casefold()] = proxy
        for name in ("NO_PROXY", "no_proxy"):
            environment[name] = ""
        return environment

    async def close(self) -> None:
        """关闭代理监听器及其活动连接。"""
        server = self._server
        self._server = None
        if server is not None:
            server.close()
            await server.wait_closed()
        tasks = tuple(self._connections)
        for task in tasks:
            if not task.done():
                task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._connections.clear()

    async def _accept(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        """注册单个客户端连接并保证任务退出时解除索引。"""
        task = asyncio.current_task()
        if task is not None:
            self._connections.add(task)
        try:
            await self._handle_client(reader, writer)
        finally:
            if task is not None:
                self._connections.discard(task)
            writer.close()
            await writer.wait_closed()

    async def _handle_client(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        """解析 HTTP 代理请求、执行策略并转发允许的请求。"""
        try:
            first = await reader.read(1)
            if not first:
                return None
            if first == b"\x05":
                await self._handle_socks5(reader, writer)
                return None
            raw_head = first + await reader.readuntil(b"\r\n\r\n")
        except (asyncio.IncompleteReadError, asyncio.LimitOverrunError):
            return None
        try:
            head = raw_head.decode("latin-1")
            request_line, headers = _parse_head(head)
        except ValueError:
            await _write_response(writer, 400, "malformed proxy request")
            return None

        method, target, version = request_line
        try:
            network_target, outbound_head = _target_request(
                method,
                target,
                version,
                headers,
            )
        except ValueError as error:
            await _write_response(writer, 400, str(error))
            return None

        if not await self._allow_target(network_target):
            await _write_response(writer, 403, "network target is not allowed")
            return None

        try:
            upstream_host = await _safe_upstream_host(network_target.host)
        except _UnsafeNetworkTarget:
            await _write_response(writer, 403, "local network target is not allowed")
            return None

        try:
            upstream_reader, upstream_writer = await asyncio.open_connection(
                upstream_host,
                network_target.port,
            )
        except (OSError, asyncio.TimeoutError):
            await _write_response(writer, 502, "network target is unavailable")
            return None

        try:
            if method == "CONNECT":
                writer.write(b"HTTP/1.1 200 Connection Established\r\n\r\n")
                await writer.drain()
                await asyncio.gather(
                    _pipe(reader, upstream_writer),
                    _pipe(upstream_reader, writer),
                )
            else:
                upstream_writer.write(outbound_head)
                content_length = _content_length(headers)
                if content_length:
                    upstream_writer.write(await reader.readexactly(content_length))
                await upstream_writer.drain()
                await _pipe(upstream_reader, writer)
        except (BrokenPipeError, ConnectionResetError, asyncio.IncompleteReadError):
            return None
        finally:
            upstream_writer.close()
            await upstream_writer.wait_closed()

    async def _allow_target(self, target: NetworkTarget) -> bool:
        """评估目标并在阻断时等待统一审批回调。"""
        if self.policy.decide(target, session_id=self.session_id) is NetworkDecision.ALLOW:
            return True
        callback = self._on_blocked
        if callback is not None:
            try:
                await callback(BlockedNetworkRequest(target, "static_policy_denied"))
            except Exception:
                return False
        return self.policy.decide(target, session_id=self.session_id) is NetworkDecision.ALLOW

    async def _handle_socks5(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        """处理 SOCKS5 无认证 CONNECT，并复用同一网络策略。"""
        try:
            method_count = (await reader.readexactly(1))[0]
            methods = await reader.readexactly(method_count)
        except asyncio.IncompleteReadError:
            return None
        if 0 not in methods:
            writer.write(b"\x05\xff")
            await writer.drain()
            return None
        writer.write(b"\x05\x00")
        await writer.drain()

        try:
            version, command, _reserved, address_type = await reader.readexactly(4)
            if version != 5:
                return None
            host = await _read_socks5_host(reader, address_type)
            port = int.from_bytes(await reader.readexactly(2), "big")
        except (asyncio.IncompleteReadError, ValueError):
            await _write_socks5_reply(writer, 0x01)
            return None
        if command != 0x01:
            await _write_socks5_reply(writer, 0x07)
            return None

        target = NetworkTarget(host, NetworkProtocol.SOCKS5_TCP, port)
        if not await self._allow_target(target):
            await _write_socks5_reply(writer, 0x02)
            return None
        try:
            upstream_host = await _safe_upstream_host(host)
        except _UnsafeNetworkTarget:
            await _write_socks5_reply(writer, 0x02)
            return None
        try:
            upstream_reader, upstream_writer = await asyncio.open_connection(
                upstream_host,
                port,
            )
        except (OSError, asyncio.TimeoutError):
            await _write_socks5_reply(writer, 0x05)
            return None

        try:
            writer.write(b"\x05\x00\x00\x01\x00\x00\x00\x00\x00\x00")
            await writer.drain()
            await asyncio.gather(
                _pipe(reader, upstream_writer),
                _pipe(upstream_reader, writer),
            )
        except (BrokenPipeError, ConnectionResetError, asyncio.IncompleteReadError):
            return None
        finally:
            upstream_writer.close()
            await upstream_writer.wait_closed()


def _parse_head(head: str) -> tuple[tuple[str, str, str], dict[str, str]]:
    """解析请求头并拒绝缺少必要字段的请求。"""
    lines = head.split("\r\n")
    if not lines or len(lines[0].split()) != 3:
        raise ValueError("malformed request line")
    method, target, version = lines[0].split()
    headers: dict[str, str] = {}
    for line in lines[1:]:
        if not line:
            continue
        if ":" not in line:
            raise ValueError("malformed request header")
        name, value = line.split(":", 1)
        headers[name.strip().casefold()] = value.strip()
    return (method.upper(), target, version), headers


def _target_request(
    method: str,
    target: str,
    version: str,
    headers: Mapping[str, str],
) -> tuple[NetworkTarget, bytes]:
    """从 CONNECT authority 或 HTTP URL 构造网络目标与转发头。"""
    if method == "CONNECT":
        parsed = urlsplit(f"https://{target}")
        protocol = NetworkProtocol.HTTPS
        host = parsed.hostname
        port = parsed.port if parsed.port is not None else 443
        if not host:
            raise ValueError("CONNECT target host is required")
        return NetworkTarget(host, protocol, port), b""

    parsed = urlsplit(target)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        host_header = headers.get("host", "")
        parsed = urlsplit(f"http://{host_header}{target}")
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("HTTP proxy target must include a host")
    protocol = (
        NetworkProtocol.HTTP
        if parsed.scheme == "http"
        else NetworkProtocol.HTTPS
    )
    port = parsed.port if parsed.port is not None else (
        80 if protocol is NetworkProtocol.HTTP else 443
    )
    path = parsed.path or "/"
    if parsed.query:
        path = f"{path}?{parsed.query}"
    outbound = [f"{method} {path} {version}\r\n"]
    for name, value in headers.items():
        if name in {"proxy-connection", "proxy-authorization"}:
            continue
        outbound.append(f"{name}: {value}\r\n")
    outbound.append("connection: close\r\n\r\n")
    return NetworkTarget(parsed.hostname, protocol, port), "".join(outbound).encode("latin-1")


def _content_length(headers: Mapping[str, str]) -> int:
    """读取可选 HTTP 请求体长度，非法值按零处理。"""
    value = headers.get("content-length", "").strip()
    try:
        length = int(value)
    except ValueError:
        return 0
    return length if length > 0 else 0


async def _pipe(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
) -> None:
    """在代理两端转发字节直到任一端关闭。"""
    while True:
        chunk = await reader.read(65536)
        if not chunk:
            return None
        writer.write(chunk)
        await writer.drain()


async def _read_socks5_host(
    reader: asyncio.StreamReader,
    address_type: int,
) -> str:
    """读取 SOCKS5 域名、IPv4 或 IPv6 目标地址。"""
    if address_type == 0x01:
        return str(ipaddress.ip_address(await reader.readexactly(4)))
    if address_type == 0x04:
        return str(ipaddress.ip_address(await reader.readexactly(16)))
    if address_type == 0x03:
        length = (await reader.readexactly(1))[0]
        if length == 0:
            raise ValueError("SOCKS5 domain is empty")
        return (await reader.readexactly(length)).decode("idna")
    raise ValueError("SOCKS5 address type is unsupported")


async def _write_socks5_reply(
    writer: asyncio.StreamWriter,
    reply: int,
) -> None:
    """写入 SOCKS5 最小响应并保持连接由调用方收束。"""
    writer.write(bytes((5, reply, 0, 1, 0, 0, 0, 0, 0, 0)))
    await writer.drain()


async def _write_response(
    writer: asyncio.StreamWriter,
    status: int,
    message: str,
) -> None:
    """返回最小 HTTP 错误响应并关闭连接。"""
    body = message.encode("utf-8", errors="replace")
    writer.write(
        f"HTTP/1.1 {status} Error\r\n"
        f"Content-Length: {len(body)}\r\n"
        "Connection: close\r\n\r\n".encode("latin-1")
        + body
    )
    await writer.drain()


def _target_key(target: NetworkTarget) -> tuple[str, NetworkProtocol, int]:
    """返回运行时授权使用的规范化目标键。"""
    return (target.host.casefold().rstrip("."), target.protocol, target.port)


async def _safe_upstream_host(host: str) -> str:
    """解析域名并拒绝解析到本地或私网地址的主机名。"""
    normalized = host.strip().rstrip(".")
    if not normalized:
        raise _UnsafeNetworkTarget("network target host is empty")
    try:
        ipaddress.ip_address(normalized)
    except ValueError:
        if normalized.casefold() == "localhost":
            return normalized
        try:
            infos = await asyncio.wait_for(
                asyncio.get_running_loop().getaddrinfo(
                    normalized,
                    None,
                    type=socket.SOCK_STREAM,
                ),
                timeout=2.0,
            )
        except OSError as error:
            raise _UnsafeNetworkTarget(
                "network target hostname could not be resolved"
            ) from error
        except asyncio.TimeoutError as error:
            raise _UnsafeNetworkTarget(
                "network target hostname resolution timed out"
            ) from error
        addresses = {
            str(sockaddr[0]).strip()
            for _family, _socktype, _proto, _canonname, sockaddr in infos
            if sockaddr and str(sockaddr[0]).strip()
        }
        if not addresses:
            raise _UnsafeNetworkTarget(
                "network target hostname has no resolved addresses"
            )
        if any(_is_local_private_address(address) for address in addresses):
            raise _UnsafeNetworkTarget(
                "network target hostname resolves to a local address"
            )
        return sorted(addresses)[0]
    else:
        return normalized


def _is_local_private_address(value: str) -> bool:
    """判断地址是否属于回环、私网、链路本地或保留地址。"""
    try:
        address = ipaddress.ip_address(value)
    except ValueError:
        return False
    return bool(
        address.is_loopback
        or address.is_private
        or address.is_link_local
        or address.is_reserved
        or address.is_unspecified
    )


if __name__ == '__main__':
    pass
