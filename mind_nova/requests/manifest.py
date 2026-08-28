# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import sys
import httpx
import typing
import asyncio
import logging
import platform
from mind_nova.service_auth import (
    build_service_headers,
    build_service_query,
)
from mind_nova.services import service_endpoints

_LOGGER = logging.getLogger(__name__)


async def fetch_manifest() -> typing.Optional[dict[str, typing.Any]]:
    """获取当前平台对应的清单配置。"""
    headers = build_service_headers()
    params  = build_service_query() | {
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
    except Exception as error:
        task = asyncio.current_task()
        if task is not None and task.cancelling():
            raise asyncio.CancelledError from error
        _LOGGER.warning("manifest.fetch.failed", exc_info=True)
        return None

    if not isinstance(data, dict) or not data.get("ok"):
        return None

    return data.get("data")


if __name__ == '__main__':
    pass
