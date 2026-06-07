# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import time
import json
import httpx
import typing
import asyncio
import inspect
import contextlib
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from engine.tinker import MindError
from mind_app.mcp import (
    McpSessionLike,
    MultiMcpSession,
)
from mind_nova import (
    authentic, const, request
)

if typing.TYPE_CHECKING:
    from ..mind_core import Mind


def _flatten_exceptions(exc: BaseException) -> typing.Generator[BaseException, None, None]:
    """展开异常组，便于识别底层 MCP / HTTP 失败。"""
    if isinstance(exc, BaseExceptionGroup):
        for sub in exc.exceptions:
            yield from _flatten_exceptions(sub)
        return

    yield exc


def _pick_mcp_bootstrap(exc: BaseException, *, mcp_url: str) -> BaseException:
    """从异常组中提取最贴近 MCP bootstrap 的底层异常。"""
    fallback: typing.Optional[BaseException] = None

    for item in _flatten_exceptions(exc):
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


def _response_body_text(exc: httpx.HTTPStatusError) -> str:
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


def _compact_error_text(text: str) -> str:
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


def _bootstrap_failure(exc: BaseException, *, mcp_url: str) -> MindError:
    """把 MCP bootstrap 失败转换成用户可读的 CLI 错误。"""
    root = _pick_mcp_bootstrap(exc, mcp_url=mcp_url)

    if isinstance(root, httpx.HTTPStatusError):
        status_code = root.response.status_code if root.response else 0
        detail = _compact_error_text(_response_body_text(root))
        if detail:
            return MindError(
                f"MCP bootstrap failed: {status_code} from {const.MCP_ED} | {detail}"
            )
        return MindError(
            f"MCP bootstrap failed: {status_code} from {const.MCP_ED}"
        )

    if isinstance(root, httpx.ConnectError):
        return MindError(
            f"MCP bootstrap failed: unable to connect to local helix at {const.BASE_URL}"
        )

    if isinstance(root, httpx.TimeoutException):
        return MindError(
            f"MCP bootstrap failed: timeout while preparing local helix session"
        )

    if isinstance(root, httpx.RemoteProtocolError):
        return MindError(
            f"MCP bootstrap failed: local helix returned an invalid HTTP response"
        )

    if isinstance(root, httpx.ProxyError):
        return MindError(
            f"MCP bootstrap failed: proxy error while preparing local helix session"
        )

    return MindError(
        f"MCP bootstrap failed: {type(root).__name__}: {root}"
    )


async def with_mcp_session(
    mind: "Mind",
    pref_config: dict[str, typing.Any],
    function: typing.Callable[
        [
            McpSessionLike,
            list[dict[str, typing.Any]],
            dict[str, dict[str, typing.Any]],
        ],
        typing.Awaitable[None]
    ],
    before_user_flow: typing.Optional[typing.Callable[[], typing.Any]] = None,
) -> None:
    """建立共享 MCP 会话，并把工具信息注入到调用流程。"""
    async def inject_auth(req: httpx.Request) -> None:
        """为 MCP 请求注入短时 Bearer 凭证。"""
        now = int(time.time())
        if not token_cache["val"] or now - token_cache["ts"] >= 60:
            token_cache["val"] = authentic.manufacture_token()
            token_cache["ts"] = now
        req.headers["Authorization"] = f"Bearer {token_cache['val']}"

    mind.ensure_pref_config(pref_config)

    url         = const.BASE_URL + const.MCP_ED
    token_cache = {"ts": 0, "val": ""}
    timeout     = httpx.Timeout(connect=10.0, read=None, write=10.0, pool=10.0)
    event_hooks = {"request": [inject_auth], "response": [request.cap_response]}

    async with httpx.AsyncClient(timeout=timeout, event_hooks=event_hooks, trust_env=False) as client:
        entered_user_flow = False

        try:
            async with streamable_http_client(url, http_client=client) as (r, w, _):
                async with ClientSession(r, w) as session:
                    await session.initialize()
                    external_group = mind.external_mcp.group if mind.external_mcp else None
                    active_session = MultiMcpSession(session, external_group)

                    list_tools = await active_session.list_tools()

                    openai_tools, tool_meta = mind.build_openai_tools(list_tools)

                    if before_user_flow is not None:
                        callback_result = before_user_flow()
                        if inspect.isawaitable(callback_result):
                            await callback_result

                    entered_user_flow = True
                    await function(active_session, openai_tools, tool_meta)

        except BaseException as exc:
            if isinstance(exc, (asyncio.CancelledError, KeyboardInterrupt, SystemExit, MindError)):
                raise
            if entered_user_flow:
                raise
            raise _bootstrap_failure(exc, mcp_url=url) from None


if __name__ == '__main__':
    pass
