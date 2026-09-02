# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import time
import typing
from contextlib import asynccontextmanager
from datetime import timedelta

import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

from infrastructure.mcp.errors import (
    flatten_exceptions,
    is_transport_close_exception,
)
from observability import observe_exception
from protocol.transport import config
from protocol.transport.auth import manufacture_token
from protocol.transport.streaming import cap_response

LOCAL_MCP_READ_TIMEOUT: timedelta = timedelta(minutes=30)


def _wraps_user_flow_error(exc: BaseException, user_flow_error: BaseException) -> bool:
    """判断 SDK 退出异常是否只包装了业务异常和关闭噪音。"""
    leaves = list(flatten_exceptions(exc))
    if not any(item is user_flow_error for item in leaves):
        return False

    for item in leaves:
        if item is user_flow_error:
            continue
        if not is_transport_close_exception(item):
            return False

    return True


@asynccontextmanager
async def open_local_mcp_session() -> typing.AsyncIterator[ClientSession]:
    """打开内置 MCP 会话，并在退出时释放底层 HTTP stream。"""
    token_cache = {"ts": 0, "val": ""}
    user_flow_error: BaseException | None = None

    async def inject_auth(req: httpx.Request) -> None:
        """为 MCP 请求注入短时 Bearer 凭证。"""
        now = int(time.time())
        if not token_cache["val"] or now - token_cache["ts"] >= 60:
            token_cache["val"] = manufacture_token()
            token_cache["ts"] = now
        req.headers["Authorization"] = f"Bearer {token_cache['val']}"

    timeout = httpx.Timeout(connect=10.0, read=None, write=10.0, pool=10.0)
    event_hooks = {"request": [inject_auth], "response": [cap_response]}

    async with httpx.AsyncClient(timeout=timeout, event_hooks=event_hooks, trust_env=False) as client:
        try:
            async with streamable_http_client(config.BASE_URL + config.MCP_ED, http_client=client) as (r, w, _):
                async with ClientSession(r, w, read_timeout_seconds=LOCAL_MCP_READ_TIMEOUT) as session:
                    await session.initialize()
                    try:
                        yield session
                    except BaseException as exc:
                        user_flow_error = exc
                        raise

        except BaseException as exc:
            if user_flow_error is not None and _wraps_user_flow_error(exc, user_flow_error):
                raise user_flow_error from None

            if user_flow_error is None and is_transport_close_exception(exc):
                observe_exception(
                    "mcp.local.close_ignored",
                    exc,
                    level="WARNING",
                )
                return

            raise


if __name__ == '__main__':
    pass
