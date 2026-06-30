# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import json
import httpx
import typing
import contextlib
from engine.tinker import MindError
from mind_app.mcp.errors import (
    exception_type_name, flatten_exceptions
)
from mind_nova import const


def pick_mcp_bootstrap(exc: BaseException, *, mcp_url: str) -> BaseException:
    """从异常组中提取最贴近 MCP bootstrap 的底层异常。"""
    fallback: typing.Optional[BaseException] = None

    for item in flatten_exceptions(exc):
        if isinstance(item, httpx.HTTPStatusError):
            req_url = str(item.request.url) if item.request else ""
            if req_url.startswith(mcp_url):
                return item
            fallback = fallback or item
            continue

        if isinstance(
            item,
            (
                httpx.ConnectError,
                httpx.ProxyError,
                httpx.TimeoutException,
                httpx.RemoteProtocolError
            )
        ):
            return item

        fallback = fallback or item

    return fallback or exc


def is_transport_close_exception(exc: BaseException) -> bool:
    """判断异常组是否只包含 MCP 关闭期可忽略的传输断开异常。"""
    transport_close_types = {
        "httpx.ReadError",
        "httpx.WriteError",
        "httpx.CloseError",
        "httpcore.ReadError",
        "httpcore.WriteError",
        "httpcore.CloseError",
        "anyio.EndOfStream",
        "anyio.BrokenResourceError",
        "anyio.ClosedResourceError",
    }
    items = list(flatten_exceptions(exc))
    if not items:
        return False

    return all(exception_type_name(item) in transport_close_types for item in items)


def response_body_text(exc: httpx.HTTPStatusError) -> str:
    """读取失败响应体，优先使用 response hook 已缓存的内容。"""
    body = exc.response.extensions.get("error_body", b"") if exc.response else b""
    if isinstance(body, bytes) and body:
        return body.decode(const.CHARSET, errors="replace").strip()

    if exc.response is None:
        return ""

    with contextlib.suppress(httpx.ResponseNotRead):
        body = exc.response.content
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
        status_code = exc.response.status_code if exc.response else 0
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


def bootstrap_failure(exc: BaseException, *, mcp_url: str) -> MindError:
    """把 MCP bootstrap 失败转换成用户可读的 CLI 错误。"""
    root = pick_mcp_bootstrap(exc, mcp_url=mcp_url)

    if isinstance(root, httpx.HTTPStatusError):
        status_code = root.response.status_code if root.response else 0
        detail = compact_error_text(response_body_text(root))
        if detail:
            return MindError(
                f"MCP bootstrap failed: {status_code} from {const.MCP_ED} | {detail}"
            )
        return MindError(
            f"MCP bootstrap failed: {status_code} from {const.MCP_ED}"
        )

    if isinstance(root, httpx.ConnectError):
        return MindError(
            f"MCP bootstrap failed: unable to connect to local service at {const.BASE_URL}"
        )

    if isinstance(root, httpx.TimeoutException):
        return MindError(
            f"MCP bootstrap failed: timeout while preparing local service session"
        )

    if isinstance(root, httpx.RemoteProtocolError):
        return MindError(
            f"MCP bootstrap failed: local service returned an invalid HTTP response"
        )

    if isinstance(root, httpx.ProxyError):
        return MindError(
            f"MCP bootstrap failed: proxy error while preparing local service session"
        )

    return MindError(
        f"MCP bootstrap failed: {type(root).__name__}: {root}"
    )


if __name__ == '__main__':
    pass
