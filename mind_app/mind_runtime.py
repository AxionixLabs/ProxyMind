# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import time
import httpx
import typing
import asyncio
import contextlib
from loguru import logger
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from mind_nova.events import EventReport
from mind_nova import (
    authentic, const, request
)
from .stream_ui import StreamUI

if typing.TYPE_CHECKING:
    from .mind_core import Mind


def resolve_mode_runner(
    mind: "Mind",
    mode: typing.Literal["chat", "fast", "plan"],
) -> typing.Callable[..., typing.Awaitable[None]]:
    """根据单次调用模式选择底层执行器。"""
    if mode in {"chat", "fast"}:
        return mind.stream_looper
    if mode == "plan":
        return mind.static_looper
    raise ValueError(f"Unsupported mode: {mode}")


def _flatten_exceptions(exc: BaseException) -> typing.Generator[BaseException, None, None]:
    """展开异常组，便于统一记录底层异常。"""
    if isinstance(exc, BaseExceptionGroup):
        for sub in exc.exceptions:
            yield from _flatten_exceptions(sub)
    else:
        yield exc


async def with_mcp_session(
    mind: "Mind",
    model_api: dict[str, typing.Any],
    function: typing.Callable[
        [
            ClientSession,
            list[dict[str, typing.Any]],
            dict[str, dict[str, typing.Any]],
        ],
        typing.Awaitable[None]
    ]
) -> None:
    """建立共享 MCP 会话，并把工具信息注入到调用流程。"""

    async def keepalive_loop(req_client: httpx.AsyncClient, stop_event: asyncio.Event) -> None:
        """保持 MCP 连接活跃，并动态接收服务端 keepalive 周期。"""
        keepalive_sec = float(const.KEEPALIVE_SEC)

        while not stop_event.is_set():
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=keepalive_sec)
                break
            except asyncio.TimeoutError:
                pass

            try:
                response = await req_client.get(
                    f"{const.BASE_URL}/api/keepalive",
                    headers={"accept": "application/json"},
                    timeout=float(const.KEEPALIVE_TIMEOUT_SEC),
                )
                response.raise_for_status()

                payload = (
                    response.json()
                    if response.headers.get("content-type", "").lower().startswith("application/json")
                    else {}
                )

                if isinstance(payload, dict):
                    value = payload.get("keepalive_sec")
                    if isinstance(value, (int, float)) and value > 0:
                        keepalive_sec = float(value)
            except Exception as exc:
                logger.debug(f"[Keepalive] failed: {type(exc).__name__}: {exc}")

    async def inject_auth(req: httpx.Request) -> None:
        """为 MCP 请求注入短时 Bearer 凭证。"""
        now = int(time.time())
        if not token_cache["val"] or now - token_cache["ts"] >= 60:
            token_cache["val"] = authentic.manufacture_token()
            token_cache["ts"] = now
        req.headers["Authorization"] = f"Bearer {token_cache['val']}"

    mind.ensure_model_api(model_api)

    url         = const.BASE_URL + const.MCP_ED
    token_cache = {"ts": 0, "val": ""}
    timeout     = httpx.Timeout(connect=10.0, read=None, write=10.0, pool=10.0)
    event_hooks = {"request": [inject_auth], "response": [request.cap_response]}

    async with httpx.AsyncClient(timeout=timeout, event_hooks=event_hooks, trust_env=False) as client:
        keepalive_stop = asyncio.Event()
        keepalive_task: typing.Optional[asyncio.Task[None]] = None

        try:
            async with streamable_http_client(url, http_client=client) as (r, w, _):
                async with ClientSession(r, w) as session:
                    await session.initialize()
                    list_tools = await session.list_tools()
                    openai_tools, tool_meta = mind.build_openai_tools(list_tools)

                    keepalive_task = asyncio.create_task(keepalive_loop(client, keepalive_stop))
                    await function(session, openai_tools, tool_meta)

        finally:
            keepalive_stop.set()

            if keepalive_task:
                keepalive_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await keepalive_task


async def with_mcp_guard(
    mind: "Mind",
    runner: typing.Callable[..., typing.Awaitable[None]],
    *,
    mode: typing.Literal["chat", "fast", "plan"] = "chat",
    **kwargs
) -> None:
    """为模式执行增加动画、网络异常和 HTTP 异常保护层。"""
    await mind.start_anim(mode)

    try:
        await runner(mode=mode, **kwargs)

    except* (httpx.ConnectError, httpx.ProxyError, httpx.TimeoutException) as error_group:
        for error_item in _flatten_exceptions(error_group):
            logger.error(f"❌ [Network Error] {error_item!r}")

    except* httpx.HTTPStatusError as error_group:
        for error_item in _flatten_exceptions(error_group):
            if isinstance(error_item, httpx.HTTPStatusError):
                body = error_item.response.extensions.get("error_body", b"")
                text = body.decode(const.CHARSET, errors="replace")
                logger.error(f"❌ [HTTP Error] {error_item.response.status_code} {text}")
            else:
                logger.error(f"❌ [HTTP Error] unexpected: {error_item!r}")

    except* Exception as error_group:
        for error_item in _flatten_exceptions(error_group):
            logger.error(f"❌ [Runtime Error] {error_item!r}")

    finally:
        await mind.stop_anim()


async def wakeup(
    mind: "Mind",
    session: ClientSession,
    stream_ui: typing.Optional[StreamUI] = None
) -> typing.Optional[str]:
    """按 TTL 触发设备刷新，避免高频重复 refresh。"""

    if ((now := time.time()) - mind.last_refresh_ts) < mind.ttl_sec:
        tip = f"ttl-hit: skip refresh ttl={mind.ttl_sec:.3f}s"
        if stream_ui and mind.level != const.SHOW_LEVEL:
            return await stream_ui.feed(tip, display=StreamUI.BLOCK)
        return logger.debug(tip)

    result = await session.call_tool("refresh", {"ttl_sec": mind.ttl_sec})
    ok = not result.isError
    content = result.content[0].text

    if not ok:
        return content

    mind.last_refresh_ts = now

    if stream_ui and mind.level != const.SHOW_LEVEL:
        return await stream_ui.feed(content, display=StreamUI.BLOCK)
    return logger.debug(content)


async def calling(
    mind: "Mind",
    model_api: typing.Optional[dict[str, typing.Any]] = None,
    *,
    message: str,
    mode: typing.Literal["chat", "fast", "plan"] = "chat",
    **kwargs
) -> None:
    """统一包装一次用户调用，并由 mode 决定底层执行器。"""
    model_api = model_api or mind.pref.to_config()

    runner = resolve_mode_runner(mind, mode)

    meta_in = kwargs.get("metadata") or {}
    cid = meta_in.get("cid") if isinstance(meta_in, dict) else None
    sid = meta_in.get("sid") if isinstance(meta_in, dict) else None
    kwargs["metadata"] = meta = mind.begin_session(cid=cid, sid=sid)

    event_report = kwargs.get("ev_report")
    owns_event_report = False
    if not event_report:
        event_report = EventReport(mode, meta["cid"], meta["sid"])
        kwargs["ev_report"] = event_report
        await event_report.open()
        owns_event_report = True

    async def function(
        session: ClientSession,
        openai_tools: list[dict[str, typing.Any]],
        tool_meta: dict[str, dict[str, typing.Any]],
    ) -> None:
        """在共享 MCP 会话中执行单次请求。"""
        await with_mcp_guard(
            mind,
            runner,
            session=session,
            mode=mode,
            model_api=model_api,
            message=message,
            openai_tools=openai_tools,
            tool_meta=tool_meta,
            **kwargs
        )

    try:
        return await mind.with_mcp_session(model_api, function)
    finally:
        if owns_event_report:
            await event_report.flush()
            await event_report.close()


if __name__ == '__main__':
    pass
