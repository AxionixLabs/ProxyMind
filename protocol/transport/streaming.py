# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import asyncio
import json
import time
import typing
from urllib.parse import urlsplit

import httpx

from observability import (
    observe,
    observe_exception,
)


class StreamDecodeError(httpx.DecodingError):
    """表示 SSE 数据行在传输过程中损坏。"""


async def cap_response(response: httpx.Response) -> None:
    """捕获失败响应体，便于后续调试。"""
    if response.status_code >= 400:
        try:
            response.extensions["error_body"] = await response.aread()
        except Exception as e:
            _ = e
            response.extensions["error_body"] = b""


async def streaming(
    url: str,
    headers: dict,
    payload: dict,
    timeout: float = 60.0
) -> typing.AsyncGenerator[dict, None]:
    """按 SSE `data:` 行读取并解析事件流。"""
    route = urlsplit(url).path or "/"
    started_at = time.perf_counter()
    event_count: int = 0
    invalid_lines: int = 0

    observe(
        "http.stream.start",
        method="POST",
        route=route,
        timeout_sec=timeout,
    )

    try:
        async with httpx.AsyncClient(timeout=timeout, event_hooks={"response": [cap_response]}) as client:
            async with client.stream("POST", url, headers=headers, json=payload) as resp:
                observe(
                    "http.stream.response",
                    method="POST",
                    route=route,
                    status=resp.status_code,
                    request_id=resp.headers.get("x-request-id"),
                )
                resp.raise_for_status()

                async for line in resp.aiter_lines():
                    if not line or not line.startswith("data:"):
                        continue

                    try:
                        event = json.loads(line[len("data:"):].strip())
                    except json.JSONDecodeError as error:
                        invalid_lines += 1
                        raise StreamDecodeError(
                            "stream data line is not valid JSON",
                            request=resp.request,
                        ) from error

                    event_count += 1
                    yield event
    except asyncio.CancelledError:
        observe(
            "http.stream.interrupted",
            level="WARNING",
            method="POST",
            route=route,
            events=event_count,
            elapsed_ms=int((time.perf_counter() - started_at) * 1000),
        )
        raise
    except Exception as error:
        observe_exception(
            "http.stream.failed",
            error,
            method="POST",
            route=route,
            events=event_count,
            elapsed_ms=int((time.perf_counter() - started_at) * 1000),
        )
        raise
    else:
        observe(
            "http.stream.complete",
            method="POST",
            route=route,
            events=event_count,
            invalid_lines=invalid_lines,
            elapsed_ms=int((time.perf_counter() - started_at) * 1000),
        )


if __name__ == '__main__':
    pass
