# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import sys
import httpx
import typing
import asyncio
import platform
from engine.observability import observe_exception
from engine.channel import Channel
from engine import signals
from mind_nova.services import service_endpoints


async def fetch_manifest() -> typing.Optional[dict[str, typing.Any]]:
    """获取当前平台对应的清单配置。"""
    headers = Channel.make_headers()
    params  = Channel.make_params() | {
        "station" : sys.platform,
        "arch"    : platform.machine()
    }

    try:
        async with httpx.AsyncClient(timeout=2.0) as client:
            resp = await client.request(
                "GET",
                service_endpoints.endpoint("/mind-manifest"),
                headers=headers,
                params=params
            )
            resp.raise_for_status()
            data = resp.json()

    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise
    except Exception as e:
        if signals.task_interrupt_active():
            raise asyncio.CancelledError from e
        observe_exception("manifest.fetch.failed", e, level="WARNING")
        return None

    if not isinstance(data, dict) or not data.get("ok"):
        return None

    return data.get("data")


if __name__ == '__main__':
    pass
