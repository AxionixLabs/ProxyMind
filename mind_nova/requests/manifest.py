# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import sys
import httpx
import typing
import platform
from loguru import logger
from engine.channel import Channel
from mind_nova.services import endpoint


async def fetch_manifest() -> typing.Optional[dict[str, typing.Any]]:
    """获取当前平台对应的清单配置。"""
    headers = Channel.make_headers()
    params  = Channel.make_params() | {
        "station" : sys.platform,
        "arch"    : platform.machine()
    }

    try:
        async with httpx.AsyncClient(timeout=2.0) as client:
            resp = await client.request("GET", endpoint("/mind-manifest"), headers=headers, params=params)
            resp.raise_for_status()
            data = resp.json()

    except Exception as e:
        return logger.debug(f"[Manifest] fetch failed: {type(e).__name__}: {e}")

    if not isinstance(data, dict) or not data.get("ok"):
        return None

    return data.get("data")


if __name__ == '__main__':
    pass
