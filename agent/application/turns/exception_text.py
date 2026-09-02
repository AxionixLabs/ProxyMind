# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import contextlib
import json

import httpx

from metadata import const


def response_body_text(exc: httpx.HTTPStatusError) -> str:
    """读取失败响应体，优先使用 response hook 已缓存的内容。"""
    response = exc.response
    body = response.extensions.get("error_body", b"")
    if isinstance(body, bytes) and body:
        return body.decode(const.CHARSET, errors="replace").strip()

    with contextlib.suppress(httpx.ResponseNotRead):
        body = response.content
        if isinstance(body, bytes) and body:
            return body.decode(const.CHARSET, errors="replace").strip()

    return ""


def compact_error_text(text: str) -> str:
    """把 JSON / 文本错误体压成一行摘要。"""
    raw = str(text or "").strip()
    if not raw:
        return ""

    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return " ".join(raw.split())[:220]

    if isinstance(payload, dict):
        parts: list[str] = []
        for key in ("error", "detail", "error_description", "message"):
            value = payload.get(key)
            if isinstance(value, str) and value.strip():
                parts.append(value.strip())
        if parts:
            return " | ".join(parts)[:220]

    return " ".join(raw.split())[:220]


def friendly_exception_text(exc: BaseException) -> str:
    """把运行期异常转换成用户可读的一行摘要。"""
    if isinstance(exc, httpx.HTTPStatusError):
        status_code = exc.response.status_code
        detail = compact_error_text(response_body_text(exc))
        if detail:
            return f"HTTP {status_code}: {detail}"
        return f"HTTP {status_code}"

    if isinstance(exc, httpx.ConnectError):
        return "Network error: unable to connect"

    if isinstance(exc, httpx.TimeoutException):
        return "Network error: request timed out"

    if isinstance(exc, httpx.RemoteProtocolError):
        return "Network error: invalid HTTP response"

    if isinstance(exc, httpx.ProxyError):
        return "Network error: proxy error"

    text = str(exc).strip()
    return f"{type(exc).__name__}: {text}" if text else type(exc).__name__


if __name__ == '__main__':
    pass
