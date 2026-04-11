# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import time
import httpx
import typing
import asyncio
import contextlib
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from mind_nova import (
    authentic, const, request
)
from .keepalive import run_keepalive

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

                    keepalive_task = asyncio.create_task(
                        run_keepalive(keepalive_stop, req_client=client)
                    )
                    await function(session, openai_tools, tool_meta)

        finally:
            keepalive_stop.set()

            if keepalive_task:
                keepalive_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await keepalive_task


if __name__ == '__main__':
    pass
