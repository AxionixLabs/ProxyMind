# -*- coding: utf-8 -*-
# Notes: ⦿ Helix License ⦿ Licensed runtime only — keep it private.

import time
import typing
from loguru import logger
from fastapi import Request
from backend.utilities.runtime import Idle


async def touch_middleware(request: Request, call_next: typing.Callable) -> typing.Any:
    """统一刷新 HTTP 请求的最近活动时间。"""
    idle: Idle = request.app.state.idle

    t0 = time.perf_counter()
    await idle.touch()

    try:
        response = await call_next(request)
    except Exception as e:
        elapsed_ms = int((time.perf_counter() - t0) * 1000)
        logger.error(
            f"{request.method} {request.url.path} -> ERROR ({elapsed_ms}ms) {type(e).__name__}: {e}"
        )
        raise

    elapsed_ms = int((time.perf_counter() - t0) * 1000)
    logger.debug(f"{request.method} {request.url.path} -> {response.status_code} ({elapsed_ms}ms)")
    return response


if __name__ == '__main__':
    pass
