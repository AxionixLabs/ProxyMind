# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import sys
import time
import json
import httpx
import base64
import typing
import asyncio
import hashlib
from pathlib import Path
from dataclasses import dataclass
from engine.errors import ApplicationError
from mind_nova import const
from .models import (
    CodeSourcePayload,
    CodeSourceResolved
)


@dataclass(slots=True)
class UrlCacheEntry:
    """URL 星图的进程内缓存记录。"""
    content: str
    identity: str
    fetched_at_ms: int
    expires_at_ms: int


_URL_CACHE: dict[str, UrlCacheEntry] = {}


def _now_ms() -> int:
    """返回当前毫秒时间戳。"""
    return int(time.time() * 1000)


def _prune_url_cache(now_ms: int) -> None:
    """移除已过期的 URL 星图缓存，避免长生命周期进程中只增不减。"""
    expired = [key for key, entry in _URL_CACHE.items() if entry.expires_at_ms <= now_ms]
    for key in expired:
        _URL_CACHE.pop(key, None)


def _hash_content(content: str) -> str:
    """为星图内容生成稳定摘要。"""
    return hashlib.sha256(content.encode(const.CHARSET, errors="replace")).hexdigest()


def _hash_cache_key(src: CodeSourcePayload) -> str:
    """为 URL 星图生成稳定缓存键。"""
    payload = {
        "kind"    : src.kind,
        "url"     : src.url or "",
        "headers" : src.headers or {},
        "auth"    : src.auth.to_dict() if src.auth else {},
    }
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(raw.encode(const.CHARSET, errors="replace")).hexdigest()


def _build_resolved(
    *,
    kind: typing.Literal["file", "stdin", "inline", "url"],
    name: str,
    content: str,
    display_origin: str,
    cache_hit: bool = False,
    fetched_at_ms: int | None = None
) -> CodeSourceResolved:
    """构造标准化后的执行源。"""
    return CodeSourceResolved(
        kind=kind,
        name=name,
        content=content,
        display_origin=display_origin,
        identity=f"sha256:{_hash_content(content)}",
        cache_hit=cache_hit,
        fetched_at_ms=fetched_at_ms
    )


def _normalize_source(item: typing.Any) -> CodeSourcePayload:
    """
    把外部输入归一化为统一来源 schema。

    传递示例：
    - "-" -> stdin
    - "inline:# name: smoke\n检查登录接口" -> inline
    - "https://example.com/packs/login-smoke.md" -> url
    - "/tmp/login-smoke.md" -> file
    """

    if isinstance(item, CodeSourcePayload):
        return item

    if isinstance(item, dict):
        return CodeSourcePayload.from_input(item)

    entry = str(item or "").strip()
    if not entry:
        raise ApplicationError("Code source entry is empty")

    if entry == "-":
        return CodeSourcePayload.from_input({"kind": "stdin"})

    if entry.startswith("inline:"):
        return CodeSourcePayload.from_input(
            {"kind": "inline", "content": entry[len("inline:") :], "name": "inline"}
        )

    if entry.startswith("http://") or entry.startswith("https://"):
        return CodeSourcePayload.from_input({"kind": "url", "url": entry})

    return CodeSourcePayload.from_input({"kind": "file", "path": entry})


def _resolve_file(src: CodeSourcePayload) -> CodeSourceResolved:
    """把本地文件路径解析为执行源。"""
    path = Path(str(src.path or "")).expanduser()
    if not path.exists():
        raise ApplicationError(f"File not found: {path}")

    try:
        content = path.read_text(encoding=const.CHARSET, errors="replace")
    except Exception as exc:
        raise ApplicationError(exc)

    return _build_resolved(
        kind="file",
        name=str(src.name or path.name or path),
        content=content,
        display_origin=str(path)
    )


def _resolve_stdin(stdin_content: str) -> CodeSourceResolved:
    """把标准输入解析为执行源。"""
    return _build_resolved(
        kind="stdin",
        name="stdin",
        content=stdin_content,
        display_origin="stdin"
    )


def _resolve_inline(src: CodeSourcePayload) -> CodeSourceResolved:
    """把内联文本解析为执行源。"""
    inline_name = str(src.name or "inline").strip() or "inline"
    content     = str(src.content or "")
    return _build_resolved(
        kind="inline",
        name=inline_name,
        content=content,
        display_origin=f"inline:{inline_name}"
    )


def _build_url_headers(src: CodeSourcePayload) -> dict[str, str]:
    """根据 schema 构造 URL 拉取请求头。"""
    headers = {str(k): str(v) for k, v in (src.headers or {}).items()}
    auth    = src.auth

    if auth is None:
        return headers

    if auth.type == "bearer":
        headers["Authorization"] = f"Bearer {auth.token}"
        return headers

    usr   = str(auth.username or "")
    pwd   = str(auth.password or "")
    token = base64.b64encode(f"{usr}:{pwd}".encode(const.CHARSET)).decode("ascii")
    headers["Authorization"] = f"Basic {token}"
    return headers


async def _resolve_url(src: CodeSourcePayload) -> CodeSourceResolved:
    """拉取远端 URL 星图，并应用超时与缓存策略。"""
    url         = str(src.url or "").strip()
    timeout_sec = float(src.timeout_sec or 15.0)
    max_bytes   = int(src.max_content_bytes or 2_000_000)
    ttl_sec     = int(src.cache_ttl_sec or 300)
    cache_key   = _hash_cache_key(src)
    now         = _now_ms()

    if ttl_sec > 0:
        _prune_url_cache(now)
        cached = _URL_CACHE.get(cache_key)
        if cached and cached.expires_at_ms > now:
            return CodeSourceResolved(
                kind="url",
                name=str(src.name or Path(url).name or url),
                content=cached.content,
                display_origin=url,
                identity=cached.identity,
                cache_hit=True,
                fetched_at_ms=cached.fetched_at_ms
            )

    headers = _build_url_headers(src)
    timeout = httpx.Timeout(connect=timeout_sec, read=timeout_sec, write=timeout_sec, pool=timeout_sec)

    try:
        async with httpx.AsyncClient(timeout=timeout, trust_env=False, follow_redirects=True) as client:
            response = await client.get(url, headers=headers)
            response.raise_for_status()
    except httpx.TimeoutException as exc:
        raise ApplicationError(f"Code source URL timeout: {url}") from exc
    except httpx.HTTPStatusError as exc:
        raise ApplicationError(f"Code source URL failed: {url} status={exc.response.status_code}") from exc
    except httpx.HTTPError as exc:
        raise ApplicationError(f"Code source URL request failed: {url}") from exc

    raw = response.text
    raw_bytes = len(raw.encode(const.CHARSET, errors="replace"))
    if raw_bytes > max_bytes:
        raise ApplicationError(f"Code source URL too large: {url} bytes={raw_bytes}")

    fetched_at_ms = _now_ms()
    identity = f"sha256:{_hash_content(raw)}"

    if ttl_sec > 0:
        _URL_CACHE[cache_key] = UrlCacheEntry(
            content=raw,
            identity=identity,
            fetched_at_ms=fetched_at_ms,
            expires_at_ms=fetched_at_ms + ttl_sec * 1000
        )

    return CodeSourceResolved(
        kind="url",
        name=str(src.name or Path(url).name or url),
        content=raw,
        display_origin=url,
        identity=identity,
        cache_hit=False,
        fetched_at_ms=fetched_at_ms
    )


async def resolve_code_sources(code: list[typing.Any]) -> list[CodeSourceResolved]:
    """把 `--code` 或远端 `source` 输入统一解析为执行源列表。"""
    if not code:
        raise ApplicationError("Code list is empty")

    normalized = [_normalize_source(item) for item in code]
    resolved: list[CodeSourceResolved] = []
    stdin_cache: str | None = None

    for src in normalized:
        if src.kind == "file":
            resolved.append(_resolve_file(src))
            continue

        if src.kind == "stdin":
            if stdin_cache is None:
                stdin_cache = await asyncio.to_thread(sys.stdin.read)
            if not stdin_cache.strip():
                raise ApplicationError("STDIN code source is empty")
            resolved.append(_resolve_stdin(stdin_cache))
            continue

        if src.kind == "inline":
            resolved.append(_resolve_inline(src))
            continue

        resolved.append(await _resolve_url(src))

    return resolved


if __name__ == '__main__':
    pass
