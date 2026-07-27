# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import os
import re
import math
import httpx
import shutil
import typing
import socket
import asyncio
import hashlib
import contextlib
from pathlib import Path
from datetime import timedelta
from fnmatch import fnmatchcase
from urllib.parse import urlsplit
from mcp import types as mcp_types
from mcp.client.stdio import StdioServerParameters
from mcp.client.session_group import (
    SseServerParameters,
    StreamableHttpParameters
)
from mind_nova import const

DEFAULT_MCP_TRANSPORT         = "streamable_http"
DEFAULT_MCP_START_TIMEOUT_SEC = 10.0
DEFAULT_MCP_REQ_TIMEOUT_SEC   = 60.0
DEFAULT_MCP_SSE_TIMEOUT_SEC   = 30 * 60


class McpConfigError(ValueError):
    """表示外部 MCP 配置无法解析。"""


def _positive_float(value: typing.Any, fallback: float) -> float:
    """把输入转换为正浮点数，失败时返回给定默认值。"""
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return fallback

    if not math.isfinite(number) or number <= 0:
        return fallback

    return number


def _string_map(value: typing.Any) -> dict[str, str]:
    """把映射型配置规范化为字符串键值字典。"""
    if not isinstance(value, dict):
        return {}

    return {
        str(map_key).strip(): str(map_value)
        for map_key, map_value in value.items()
        if str(map_key).strip()
    }


def _string_list(value: typing.Any) -> list[str]:
    """把列表型配置规范化为字符串列表。"""
    if not isinstance(value, list):
        return []

    return [
        str(item)
        for item in value
        if isinstance(item, (str, int, float, bool))
    ]


def _tool_patterns(value: typing.Any) -> list[str]:
    """规范化外接 MCP 工具匹配模式，并保持配置顺序。"""
    if not isinstance(value, list):
        return []

    patterns: list[str] = []
    seen: set[str]      = set()

    for item in value:
        if not isinstance(item, str):
            continue
        pattern = item.strip()
        if not pattern or pattern in seen:
            continue
        seen.add(pattern)
        patterns.append(pattern)
    return patterns


def is_mcp_tool_allowed(name: str, rules: typing.Any) -> bool:
    """按原始工具名判断外接 MCP 工具是否允许暴露。"""
    policy    = rules if isinstance(rules, dict) else {}
    tool_name = str(name)

    allow = policy.get("allow")
    if "allow" in policy and not any(
        fnmatchcase(tool_name, pattern) for pattern in list(allow or [])
    ):
        return False

    deny = policy.get("deny")
    return not any(
        fnmatchcase(tool_name, pattern) for pattern in list(deny or [])
    )


def _safe_tool_component(value: typing.Any, fallback: str) -> str:
    """生成可用于工具名片段的安全字符串。"""
    text = str(value or "").strip()
    safe = "".join(ch if ch.isalnum() or ch in {"_", "-"} else "_" for ch in text)
    safe = "_".join(part for part in safe.split("_") if part)
    return safe or fallback


def _limit_tool_name(value: str) -> str:
    """限制外部工具名长度，超长时附加摘要后缀。"""
    max_external_name_len = 2048
    if len(value) <= max_external_name_len:
        return value

    digest     = hashlib.sha1(value.encode(const.CHARSET, errors="ignore")).hexdigest()[:8]
    prefix_len = max_external_name_len - len(digest) - 1

    return f"{value[:prefix_len]}_{digest}"


def slugify_mcp_name(value: typing.Any, fallback: str = "server") -> str:
    """把服务名称转换为稳定的短横线标识。"""
    text = str(value or "").strip().lower()
    text = re.sub(r"[^a-z0-9]+", "-", text).strip("-")
    return text or fallback


def normalize_mcp_servers(raw: typing.Any) -> list[dict[str, typing.Any]]:
    """把有效配置中的 MCP 服务表规范化为内部服务列表。"""
    if not isinstance(raw, dict):
        return []

    normalized: list[dict[str, typing.Any]] = []
    seen_names: set[str]                    = set()

    for idx, (key, item) in enumerate(raw.items(), start=1):
        if not isinstance(item, dict):
            continue

        name = str(key or "").strip() or f"server-{idx}"
        slug = slugify_mcp_name(name, fallback=f"server-{idx}")

        url     = str(item.get("url", "") or "").strip()
        command = str(item.get("command", "") or "").strip()

        if command and url:
            continue
        transport = (
            "stdio"
            if command
            else "sse" if url.lower().rstrip("/").endswith("/sse")
            else DEFAULT_MCP_TRANSPORT
        )
        if transport == "stdio":
            if not command:
                continue
        elif not url:
            continue

        timeout_sec = _positive_float(
            item.get("tool_timeout_sec"),
            DEFAULT_MCP_REQ_TIMEOUT_SEC
        )
        tool_rules: dict[str, list[str]] = {}
        if "allow" in item:
            tool_rules["allow"] = _tool_patterns(item.get("allow"))
        if "deny" in item:
            tool_rules["deny"] = _tool_patterns(item.get("deny"))
        unique_slug = slug

        suffix = 2
        while unique_slug in seen_names:
            unique_slug = f"{slug}-{suffix}"
            suffix += 1
        seen_names.add(unique_slug)

        base = {
            "name": unique_slug,
            "enabled": item.get("enabled", True) is not False,
            "required": item.get("required", False) is True,
            "transport": transport,
            "startup_timeout_sec": _positive_float(
                item.get("startup_timeout_sec"),
                DEFAULT_MCP_START_TIMEOUT_SEC
            ),
            "timeout_sec": timeout_sec,
            "tools": tool_rules
        }

        if transport == "stdio":
            cwd = str(item.get("cwd", "") or "").strip()
            normalized.append(
                {
                    **base,
                    "command": command,
                    "args": _string_list(item.get("args")),
                    "env": _string_map(item.get("env")),
                    "cwd": cwd,
                    "encoding": "utf-8",
                    "encoding_error_handler": "strict"
                }
            )
            continue

        headers = _string_map(item.get("http_headers"))
        for header, environment_name in _string_map(
            item.get("env_http_headers")
        ).items():
            environment_value = os.environ.get(environment_name)
            if environment_value is not None:
                headers[header] = environment_value

        bearer_name  = str(item.get("bearer_token_env_var") or "").strip()
        bearer_token = os.environ.get(bearer_name) if bearer_name else None

        if bearer_token:
            headers["Authorization"] = f"Bearer {bearer_token}"

        normalized.append(
            {
                **base,
                "url": url,
                "headers": headers,
                "sse_read_timeout_sec": timeout_sec,
                "terminate_on_close": True
            }
        )

    return normalized


def tool_name_hook(name: str, server_info: mcp_types.Implementation) -> str:
    """生成带服务名前缀的外部工具展示名。"""
    alias     = _safe_tool_component(slugify_mcp_name(server_info.name, fallback="server"), "server")
    tool_name = _safe_tool_component(name, "tool")
    return _limit_tool_name(f"mcp__{alias}__{tool_name}")


def truncate_text(value: typing.Any, limit: int) -> str:
    """按指定长度截断展示文本。"""
    text = str(value or "").strip()
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 3)].rstrip() + "..."


def request_timeout_sec(server: dict[str, typing.Any]) -> float:
    """读取外部 MCP 服务的请求超时时间。"""
    return _positive_float(server.get("timeout_sec"), DEFAULT_MCP_REQ_TIMEOUT_SEC)


def startup_timeout_sec(server: dict[str, typing.Any]) -> float:
    """读取外部 MCP 服务的启动超时时间。"""
    return _positive_float(
        server.get("startup_timeout_sec"),
        DEFAULT_MCP_START_TIMEOUT_SEC
    )


def external_http_client(
    headers: dict[str, str] | None = None,
    timeout: httpx.Timeout | None = None,
    auth: httpx.Auth | None = None
) -> httpx.AsyncClient:
    """创建外部 HTTP/SSE MCP 服务使用的 HTTP 客户端。"""
    kwargs: dict[str, typing.Any] = {
        "follow_redirects": True,
        "timeout": timeout or httpx.Timeout(
            DEFAULT_MCP_REQ_TIMEOUT_SEC,
            read=DEFAULT_MCP_SSE_TIMEOUT_SEC
        ),
        "trust_env": False
    }
    if headers:
        kwargs["headers"] = headers
    if auth is not None:
        kwargs["auth"] = auth

    return httpx.AsyncClient(**kwargs)


def build_server_params(server: dict[str, typing.Any]) -> typing.Any:
    """按 transport 类型构造 MCP SDK 所需的服务参数对象。"""
    transport   = str(server.get("transport") or "streamable_http").strip().lower()
    timeout_sec = request_timeout_sec(server)

    if transport == "stdio":
        command = str(server.get("command") or "").strip()
        if not command:
            raise ValueError("stdio MCP server missing command")

        encoding_error_handler = str(
            server.get("encoding_error_handler") or "strict"
        ).strip().lower()
        if encoding_error_handler not in {"strict", "ignore", "replace"}:
            encoding_error_handler = "strict"

        cwd = str(server.get("cwd") or "").strip() or None

        return StdioServerParameters(
            command=command,
            args=_string_list(server.get("args")),
            env=_string_map(server.get("env")) or None,
            cwd=cwd,
            encoding=str(server.get("encoding") or "utf-8").strip() or "utf-8",
            encoding_error_handler=typing.cast(
                typing.Literal["strict", "ignore", "replace"],
                encoding_error_handler
            )
        )

    sse_read_timeout_sec = _positive_float(
        server.get("sse_read_timeout_sec"),
        DEFAULT_MCP_SSE_TIMEOUT_SEC
    )

    if transport == "sse":
        url = str(server.get("url") or "").strip()
        if not url:
            raise ValueError("sse MCP server missing url")
        return SseServerParameters(
            url=url,
            headers=dict(server.get("headers") or {}) or None,
            timeout=timeout_sec,
            sse_read_timeout=sse_read_timeout_sec,
        )

    url = str(server.get("url") or "").strip()
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

    url    = str(server.get("url") or "").strip()
    parsed = urlsplit(url)
    host   = parsed.hostname

    if not host:
        raise ValueError("MCP server missing host")

    port = parsed.port
    if port is None:
        port = 443 if parsed.scheme == "https" else 80

    timeout_sec = min(_positive_float(server.get("timeout_sec"), 30.0), 1.0)

    errors: list[str] = []

    try:
        targets = await preflight_targets(host, port)
    except OSError as exc:
        raise RuntimeError(
            f"endpoint unavailable {host}:{port} ({type(exc).__name__}: {exc})"
        ) from exc

    if not targets:
        raise RuntimeError(f"endpoint unavailable {host}:{port} (no resolved address)")

    tasks = {
        asyncio.create_task(probe_preflight_target(target, timeout_sec)): target
        for target in targets
    }
    pending = set(tasks)

    try:
        while pending:
            done, pending = await asyncio.wait(pending, return_when=asyncio.FIRST_COMPLETED)

            for task in done:
                _, address, target_port = tasks[task]

                try:
                    await task
                    return
                except BaseException as exc:
                    errors.append(f"{address}:{target_port} {type(exc).__name__}: {exc}")

    finally:
        for task in pending:
            task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)

    raise RuntimeError(f"endpoint unavailable {host}:{port} ({'; '.join(errors)})")


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


async def preflight_targets(host: str, port: int) -> list[tuple[socket.AddressFamily, str, int]]:
    """解析主机端口对应的预检连接目标。"""
    targets: list[tuple[socket.AddressFamily, str, int]] = []
    seen: set[tuple[socket.AddressFamily, str, int]]     = set()

    infos = await asyncio.get_running_loop().getaddrinfo(host, port, type=socket.SOCK_STREAM)

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
    timeout_sec: float
) -> None:
    """尝试连接单个地址目标，用于判断远端端口可达性。"""
    family, address, target_port = target

    writer: asyncio.StreamWriter | None = None

    try:
        _, writer = await asyncio.wait_for(
            asyncio.open_connection(address, target_port, family=family),
            timeout=timeout_sec,
        )
    finally:
        if writer is not None:
            writer.close()
            with contextlib.suppress(Exception):
                await writer.wait_closed()


if __name__ == '__main__':
    pass
