# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import json
import time
import httpx
import typing
import asyncio
import inspect
import contextlib
from datetime import timedelta
from loguru import logger
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

LOCAL_MCP_READ_TIMEOUT: timedelta = timedelta(minutes=30)


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


def _exception_type_name(exc: BaseException) -> str:
    """返回异常的模块限定名，用于识别可选依赖的传输异常。"""
    return f"{type(exc).__module__}.{type(exc).__name__}"


def _is_transport_close_exception(exc: BaseException) -> bool:
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
    items = list(_flatten_exceptions(exc))
    if not items:
        return False

    return all(_exception_type_name(item) in transport_close_types for item in items)


def _exception_summary(exc: BaseException) -> str:
    """提取异常组中的首个叶子异常，生成简短 debug 摘要。"""
    for item in _flatten_exceptions(exc):
        text = str(item).strip()
        if text:
            return f"{type(item).__name__}: {text}"
        return type(item).__name__

    return type(exc).__name__


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
    _ = pref_config  # 保留调用签名；模型配置完整性由服务端统一判断。

    async def inject_auth(req: httpx.Request) -> None:
        """为 MCP 请求注入短时 Bearer 凭证。"""
        now = int(time.time())
        if not token_cache["val"] or now - token_cache["ts"] >= 60:
            token_cache["val"] = authentic.manufacture_token()
            token_cache["ts"] = now
        req.headers["Authorization"] = f"Bearer {token_cache['val']}"

    url         = const.BASE_URL + const.MCP_ED
    token_cache = {"ts": 0, "val": ""}
    timeout     = httpx.Timeout(connect=10.0, read=None, write=10.0, pool=10.0)
    event_hooks = {"request": [inject_auth], "response": [request.cap_response]}

    async with httpx.AsyncClient(timeout=timeout, event_hooks=event_hooks, trust_env=False) as client:
        phase = "bootstrap"

        try:
            async with streamable_http_client(url, http_client=client) as (r, w, _):
                async with ClientSession(r, w, read_timeout_seconds=LOCAL_MCP_READ_TIMEOUT) as session:
                    await session.initialize()
                    external_group = mind.external_mcp.group if mind.external_mcp else None
                    active_session = MultiMcpSession(session, external_group)

                    list_tools = await active_session.list_tools()

                    openai_tools, tool_meta = mind.build_openai_tools(list_tools)

                    if before_user_flow is not None:
                        callback_result = before_user_flow()
                        if inspect.isawaitable(callback_result):
                            await callback_result

                    phase = "user_flow"
                    await function(active_session, openai_tools, tool_meta)
                    phase = "teardown"

        except BaseException as exc:
            if isinstance(exc, (asyncio.CancelledError, KeyboardInterrupt, SystemExit, MindError)):
                raise
            if phase == "teardown" and _is_transport_close_exception(exc):
                logger.debug(f"MCP session teardown ignored: {_exception_summary(exc)}")
                return None
            if phase != "bootstrap":
                raise
            raise _bootstrap_failure(exc, mcp_url=url) from None


if __name__ == '__main__':
    pass
