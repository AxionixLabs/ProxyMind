# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import re
import copy
import json
import math
import httpx
import typing
import socket
import asyncio
import hashlib
import contextlib
from pathlib import Path
from datetime import timedelta
from urllib.parse import urlsplit
from mcp import types as mcp_types
from mcp.client.session_group import (
    SseServerParameters, StreamableHttpParameters
)
from mind_nova import const

DEFAULT_MCP_TRANSPORT = "streamable_http"
ALLOWED_MCP_TRANSPORT = {"streamable_http", "sse"}


def _positive_float(value: typing.Any, fallback: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return fallback

    if not math.isfinite(number) or number <= 0:
        return fallback

    return number


def _string_map(value: typing.Any) -> dict[str, str]:
    if not isinstance(value, dict):
        return {}

    return {
        str(map_key).strip(): str(map_value)
        for map_key, map_value in value.items()
        if str(map_key).strip()
    }


def _safe_tool_component(value: typing.Any, fallback: str) -> str:
    text = str(value or "").strip()
    safe = "".join(ch if ch.isalnum() or ch in {"_", "-"} else "_" for ch in text)
    safe = "_".join(part for part in safe.split("_") if part)
    return safe or fallback


def _limit_tool_name(value: str) -> str:
    max_external_name_len = 2048
    if len(value) <= max_external_name_len:
        return value

    digest = hashlib.sha1(value.encode(const.CHARSET, errors="ignore")).hexdigest()[:8]
    prefix_len = max_external_name_len - len(digest) - 1
    return f"{value[:prefix_len]}_{digest}"


def slugify_mcp_name(value: typing.Any, fallback: str = "server") -> str:
    text = str(value or "").strip().lower()
    text = re.sub(r"[^a-z0-9]+", "-", text).strip("-")
    return text or fallback


def normalize_mcp_servers(raw: typing.Any) -> list[dict[str, typing.Any]]:
    if not isinstance(raw, dict):
        return []

    mapping = raw.get("mcpServers")
    if not isinstance(mapping, dict):
        return []

    normalized: list[dict[str, typing.Any]] = []
    seen_names: set[str] = set()

    for idx, (key, item) in enumerate(mapping.items(), start=1):
        if not isinstance(item, dict):
            continue

        name = str(key or "").strip() or f"server-{idx}"
        slug = slugify_mcp_name(name, fallback=f"server-{idx}")

        url = str(item.get("url", "") or "").strip()
        if not url:
            continue

        inferred_transport = item.get("transport")
        if not inferred_transport:
            inferred_transport = "sse" if url.lower().endswith("/sse") else DEFAULT_MCP_TRANSPORT

        transport = str(inferred_transport or DEFAULT_MCP_TRANSPORT).strip().lower().replace("-", "_")
        if transport == "stdio":
            continue
        if transport not in ALLOWED_MCP_TRANSPORT:
            transport = DEFAULT_MCP_TRANSPORT

        timeout_sec = _positive_float(item.get("timeout_sec"), 30.0)
        unique_slug = slug

        suffix = 2
        while unique_slug in seen_names:
            unique_slug = f"{slug}-{suffix}"
            suffix += 1
        seen_names.add(unique_slug)

        normalized.append(
            {
                "name": unique_slug,
                "enabled": item.get("enabled", True) is not False,
                "transport": transport,
                "url": url,
                "headers": _string_map(item.get("headers")),
                "timeout_sec": timeout_sec,
                "sse_read_timeout_sec": _positive_float(item.get("sse_read_timeout_sec"), 300.0),
                "terminate_on_close": item.get("terminate_on_close", True) is not False,
                "notes": str(item.get("notes", "") or "").strip(),
            }
        )

    return normalized


def mcp_servers_path(root_dir: typing.Any) -> Path:
    return Path(str(root_dir)).expanduser() / "mcp_servers.json"


def load_mcp_servers_file(root_dir: typing.Any) -> list[dict[str, typing.Any]]:
    target = mcp_servers_path(root_dir)
    try:
        raw = target.read_text(encoding=const.CHARSET)
    except FileNotFoundError:
        return []
    except OSError:
        return []

    if not raw.strip():
        return []

    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return []

    try:
        return normalize_mcp_servers(payload)
    except (TypeError, ValueError, OverflowError):
        return []


def tool_name_hook(name: str, server_info: mcp_types.Implementation) -> str:
    alias = _safe_tool_component(slugify_mcp_name(server_info.name, fallback="server"), "server")
    tool_name = _safe_tool_component(name, "tool")
    return _limit_tool_name(f"mcp__{alias}__{tool_name}")


def empty_tool_schema() -> dict[str, typing.Any]:
    return {"type": "object", "properties": {}}


def normalize_tool_schema(value: typing.Any) -> dict[str, typing.Any]:
    if not isinstance(value, dict):
        return empty_tool_schema()

    try:
        schema = copy.deepcopy(value)
    except (copy.Error, TypeError, ValueError, RecursionError):
        return empty_tool_schema()

    if not isinstance(schema, dict):
        return empty_tool_schema()

    schema_type = schema.get("type")
    if schema_type is None:
        schema["type"] = "object"
    elif schema_type != "object":
        return empty_tool_schema()

    if not isinstance(schema.get("properties"), dict):
        schema["properties"] = {}

    required = schema.get("required")
    if required is not None and not (
        isinstance(required, list) and all(isinstance(item, str) for item in required)
    ):
        schema.pop("required", None)

    try:
        json.dumps(schema, ensure_ascii=False)
    except (TypeError, ValueError, OverflowError):
        return empty_tool_schema()

    return schema


def truncate_text(value: typing.Any, limit: int) -> str:
    text = str(value or "").strip()
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 3)].rstrip() + "..."


def request_timeout_sec(server: dict[str, typing.Any]) -> float:
    return _positive_float(server.get("timeout_sec"), 30.0)


def external_http_client(
    headers: dict[str, str] | None = None,
    timeout: httpx.Timeout | None = None,
    auth: httpx.Auth | None = None
) -> httpx.AsyncClient:
    kwargs: dict[str, typing.Any] = {
        "follow_redirects" : True,
        "timeout"          : timeout or httpx.Timeout(30.0, read=300.0),
        "trust_env"        : False
    }
    if headers:
        kwargs["headers"] = headers
    if auth is not None:
        kwargs["auth"] = auth
    return httpx.AsyncClient(**kwargs)


def build_server_params(server: dict[str, typing.Any]) -> typing.Any:
    transport   = str(server.get("transport") or "streamable_http").strip().lower()
    timeout_sec = _positive_float(server.get("timeout_sec"), 30.0)

    sse_read_timeout_sec = _positive_float(server.get("sse_read_timeout_sec"), 300.0)

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


async def preflight_targets(host: str, port: int) -> list[tuple[socket.AddressFamily, str, int]]:
    targets: list[tuple[socket.AddressFamily, str, int]] = []
    seen: set[tuple[socket.AddressFamily, str, int]] = set()

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
