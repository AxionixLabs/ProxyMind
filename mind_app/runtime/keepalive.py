# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import httpx
import typing
import asyncio
import contextlib
from loguru import logger
from mind_nova import const

if typing.TYPE_CHECKING:
    from engine.manage import ServerManage


async def run_keepalive(
    stop_event: asyncio.Event,
    *,
    req_client: httpx.AsyncClient | None = None,
    server_manager: "ServerManage | None" = None
) -> None:
    """后台定时执行 keepalive，并接受服务端返回的动态周期。"""
    keepalive_sec = float(const.KEEPALIVE_SEC)
    owns_client   = req_client is None

    client = req_client or httpx.AsyncClient(
        timeout=httpx.Timeout(float(const.KEEPALIVE_TIMEOUT_SEC), connect=1.5)
    )

    try:
        while not stop_event.is_set():
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=keepalive_sec)
                break
            except asyncio.TimeoutError:
                pass

            try:
                response = await client.get(
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
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.debug(f"[Keepalive] failed: {type(exc).__name__}: {exc}")
                if server_manager is not None:
                    with contextlib.suppress(Exception):
                        await server_manager.ensure_running()
    finally:
        if owns_client:
            with contextlib.suppress(Exception):
                await client.aclose()


if __name__ == '__main__':
    pass
