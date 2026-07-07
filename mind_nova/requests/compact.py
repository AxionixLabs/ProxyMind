# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import json
import httpx
import typing
from engine.channel import Channel
from mind_nova.requests.payload import (
    request_llm_conf,
    resolve_transport_mode
)
from mind_nova.services import service_endpoints

COMPACT_DEFAULT_STRATEGY = "memento"


def build_compact_payload(raw: typing.Any) -> dict[str, typing.Any]:
    """构建手动上下文压缩请求载荷。"""
    data = raw if isinstance(raw, dict) else {}

    return {
        "mode"     : resolve_transport_mode(data.get("mode") or "chat"),
        "cid"      : str(data.get("cid") or "").strip(),
        "sid"      : str(data.get("sid") or "").strip(),
        "llm_conf" : request_llm_conf(data.get("llm_conf")),
        "strategy" : str(data.get("strategy") or COMPACT_DEFAULT_STRATEGY).strip() or COMPACT_DEFAULT_STRATEGY
    }


async def stream_compact_events(
    payload: dict[str, typing.Any],
    *,
    timeout: float = 120.0
) -> typing.AsyncGenerator[dict[str, typing.Any], None]:
    """按 SSE 读取远端会话压缩事件。"""
    headers = Channel.make_headers()

    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            async with client.stream(
                "POST",
                service_endpoints.endpoint("/compact"),
                headers=headers,
                json=payload
            ) as response:
                if response.status_code >= 400:
                    body = await response.aread()
                    yield compact_failed_event(
                        status_code=response.status_code,
                        error=body.decode("utf-8", errors="replace").strip()
                    )
                    return

                async for line in response.aiter_lines():
                    if not line or not line.startswith("data:"):
                        continue

                    event = _decode_sse_data(line[len("data:"):].strip())
                    if event is not None:
                        yield event
    except httpx.HTTPError as error:
        yield compact_failed_event(status_code=502, error=str(error))


def compact_failed_event(
    *,
    status_code: int = 502,
    error: str = "",
    message: str = ""
) -> dict[str, typing.Any]:
    """构建本地压缩失败事件。"""
    code = int(status_code or 502)

    return {
        "type"        : "conversation.compact.failed",
        "status"      : "failed",
        "status_code" : code,
        "message"     : message or compact_failure_message(code),
        "error"       : str(error or "").strip()
    }


def compact_failure_message(status_code: int) -> str:
    """返回压缩失败事件的默认提示文案。"""
    if status_code == 404:
        return "There is no conversation history to compact."
    if status_code == 409:
        return "A tool call is still running. Try again after it finishes."
    return "Context compaction failed. Please try again."


def _decode_sse_data(data: str) -> dict[str, typing.Any] | None:
    """解析单行 SSE data JSON。"""
    try:
        event = json.loads(data)
    except json.JSONDecodeError:
        return None
    return event if isinstance(event, dict) else None


if __name__ == '__main__':
    pass
