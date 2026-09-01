# -*- coding: utf-8 -*-

import asyncio
import contextlib
import shutil
import socket
import typing
from datetime import timedelta
from pathlib import Path
from urllib.parse import urlsplit

import httpx
from mcp.client.session_group import (
    SseServerParameters,
    StreamableHttpParameters,
)
from mcp.client.stdio import StdioServerParameters

from .settings import (
    DEFAULT_MCP_REQ_TIMEOUT_SEC,
    DEFAULT_MCP_SSE_TIMEOUT_SEC,
    positive_float,
    request_timeout_sec,
    string_list,
    string_map,
)


def external_http_client(
    headers: dict[str, str] | None = None,
    timeout: httpx.Timeout | None = None,
    auth: httpx.Auth | None = None,
) -> httpx.AsyncClient:
    """创建外部 HTTP/SSE MCP 服务使用的 HTTP 客户端。"""
    kwargs: dict[str, typing.Any] = {
        "follow_redirects": True,
        "timeout": timeout or httpx.Timeout(
            DEFAULT_MCP_REQ_TIMEOUT_SEC,
            read=DEFAULT_MCP_SSE_TIMEOUT_SEC,
        ),
        "trust_env": False,
    }
    if headers:
        kwargs["headers"] = headers
    if auth is not None:
        kwargs["auth"] = auth
    return httpx.AsyncClient(**kwargs)


def _encoding_error_handler(
    value: typing.Any,
) -> typing.Literal["strict", "ignore", "replace"]:
    """校验并返回 SDK 支持的文本解码错误策略。"""
    normalized = str(value or "strict").strip().lower()
    if normalized == "ignore":
        return "ignore"
    if normalized == "replace":
        return "replace"
    return "strict"


def build_server_params(server: dict[str, typing.Any]) -> typing.Any:
    """按 transport 类型构造 MCP SDK 所需的服务参数对象。"""
    transport = str(
        server.get("transport") or "streamable_http"
    ).strip().lower()
    timeout_sec = request_timeout_sec(server)

    if transport == "stdio":
        command = str(server.get("command") or "").strip()
        if not command:
            raise ValueError("stdio MCP server missing command")
        cwd = str(server.get("cwd") or "").strip() or None
        return StdioServerParameters(
            command=command,
            args=string_list(server.get("args")),
            env=string_map(server.get("env")) or None,
            cwd=cwd,
            encoding=str(
                server.get("encoding") or "utf-8"
            ).strip() or "utf-8",
            encoding_error_handler=_encoding_error_handler(
                server.get("encoding_error_handler")
            ),
        )

    sse_read_timeout_sec = positive_float(
        server.get("sse_read_timeout_sec"),
        DEFAULT_MCP_SSE_TIMEOUT_SEC,
    )
    url = str(server.get("url") or "").strip()

    if transport == "sse":
        if not url:
            raise ValueError("sse MCP server missing url")
        return SseServerParameters(
            url=url,
            headers=dict(server.get("headers") or {}) or None,
            timeout=timeout_sec,
            sse_read_timeout=sse_read_timeout_sec,
        )

    if not url:
        raise ValueError("streamable_http MCP server missing url")
    return StreamableHttpParameters(
        url=url,
        headers=dict(server.get("headers") or {}) or None,
        timeout=timedelta(seconds=timeout_sec),
        sse_read_timeout=timedelta(seconds=sse_read_timeout_sec),
        terminate_on_close=bool(server.get("terminate_on_close", True)),
    )


async def preflight_server(server: dict[str, typing.Any]) -> None:
    """对外部 MCP 服务执行连接前检查。"""
    if str(server.get("transport") or "").strip().lower() == "stdio":
        await asyncio.to_thread(preflight_stdio_server, server)
        return None

    url = str(server.get("url") or "").strip()
    parsed = urlsplit(url)
    host = parsed.hostname
    if not host:
        raise ValueError("MCP server missing host")

    port = parsed.port
    if port is None:
        port = 443 if parsed.scheme == "https" else 80
    timeout_sec = min(positive_float(server.get("timeout_sec"), 30.0), 1.0)
    errors: list[str] = []

    try:
        targets = await preflight_targets(host, port)
    except OSError as error:
        raise RuntimeError(
            f"endpoint unavailable {host}:{port} "
            f"({type(error).__name__}: {error})"
        ) from error

    if not targets:
        raise RuntimeError(
            f"endpoint unavailable {host}:{port} (no resolved address)"
        )

    tasks = {
        asyncio.create_task(
            probe_preflight_target(target, timeout_sec)
        ): target
        for target in targets
    }
    pending = set(tasks)

    try:
        while pending:
            done, pending = await asyncio.wait(
                pending,
                return_when=asyncio.FIRST_COMPLETED,
            )
            for task in done:
                _, address, target_port = tasks[task]
                try:
                    await task
                    return None
                except BaseException as error:
                    errors.append(
                        f"{address}:{target_port} "
                        f"{type(error).__name__}: {error}"
                    )
    finally:
        for task in pending:
            task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)

    raise RuntimeError(
        f"endpoint unavailable {host}:{port} ({'; '.join(errors)})"
    )


def preflight_stdio_server(server: dict[str, typing.Any]) -> None:
    """对 stdio MCP 服务执行不启动进程的静态检查。"""
    command = str(server.get("command") or "").strip()
    if not command:
        raise ValueError("stdio MCP server missing command")

    cwd = str(server.get("cwd") or "").strip()
    if cwd and not Path(cwd).expanduser().is_dir():
        raise RuntimeError(f"stdio cwd unavailable: {cwd}")

    command_path = Path(command).expanduser()
    if command_path.parent != Path(".") or command_path.is_absolute():
        if not command_path.exists():
            raise RuntimeError(f"stdio command unavailable: {command}")
        if command_path.is_dir():
            raise RuntimeError(f"stdio command is a directory: {command}")
        return None

    if shutil.which(command) is None:
        raise RuntimeError(f"stdio command unavailable: {command}")
    return None


async def preflight_targets(
    host: str,
    port: int,
) -> list[tuple[socket.AddressFamily, str, int]]:
    """解析主机端口对应的预检连接目标。"""
    targets: list[tuple[socket.AddressFamily, str, int]] = []
    seen: set[tuple[socket.AddressFamily, str, int]] = set()
    infos = await asyncio.get_running_loop().getaddrinfo(
        host,
        port,
        type=socket.SOCK_STREAM,
    )

    for family, _, _, _, sockaddr in infos:
        address = str(sockaddr[0])
        target_port = int(sockaddr[1])
        key = (family, address, target_port)
        if key in seen:
            continue
        seen.add(key)
        targets.append(key)

    targets.sort(key=lambda item: 0 if item[0] == socket.AF_INET else 1)
    return targets


async def probe_preflight_target(
    target: tuple[socket.AddressFamily, str, int],
    timeout_sec: float,
) -> None:
    """尝试连接单个地址目标，用于判断远端端口可达性。"""
    family, address, target_port = target
    writer: asyncio.StreamWriter | None = None

    try:
        _, writer = await asyncio.wait_for(
            asyncio.open_connection(
                address,
                target_port,
                family=family,
            ),
            timeout=timeout_sec,
        )
    finally:
        if writer is not None:
            writer.close()
            with contextlib.suppress(Exception):
                await writer.wait_closed()
