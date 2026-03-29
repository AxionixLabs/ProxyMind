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
from mind_nova import (
    authentic, const, request
)

if typing.TYPE_CHECKING:
    from ..mind_core import Mind


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
