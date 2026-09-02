# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import sys
import httpx
import typing
import asyncio
import platform
from protocol.transport.auth import (
    build_service_headers,
    build_service_query,
)
from protocol.transport.endpoints import service_endpoints
from observability import observe_exception


async def fetch_manifest() -> typing.Optional[dict[str, typing.Any]]:
    """获取当前平台对应的清单配置。"""
    headers = build_service_headers()
    params = build_service_query() | {
        "station": sys.platform,
        "arch": platform.machine()
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
        observe_exception("manifest.fetch.failed", error, level="WARNING")
        return None

    if not isinstance(data, dict) or not data.get("ok"):
        return None

    return data.get("data")


if __name__ == '__main__':
    pass
