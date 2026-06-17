# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import json
import httpx
import typing
from loguru import logger


async def cap_request(req: httpx.Request) -> None:
    """记录请求摘要，便于排查鉴权和链路问题。"""
    a = req.headers.get("Authorization", "")
    logger.debug(
        f"[MCP] {req.method} {req.url} auth={'OK' if a.startswith('Bearer ') else 'MISSING'}"
    )


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
    async with httpx.AsyncClient(timeout=timeout, event_hooks={"response": [cap_response]}) as client:
        async with client.stream("POST", url, headers=headers, json=payload) as resp:
            resp.raise_for_status()

            async for line in resp.aiter_lines():
                if not line or not line.startswith("data:"):
                    continue

                try:
                    event = json.loads(line[len("data:"):].strip())
                except json.JSONDecodeError:
                    continue

                yield event


if __name__ == '__main__':
    pass
