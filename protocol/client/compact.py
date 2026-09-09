# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import contextlib
import typing

import httpx

from protocol.client.payload import request_llm_conf
from protocol.schema.json_value import (
    JsonObject,
    JsonValue,
)
from protocol.schema.stream_events import (
    ContextCompactionEvent,
    parse_compact_event,
)
from protocol.transport.auth import build_service_headers
from protocol.transport.endpoints import service_endpoints
from protocol.transport.streaming import streaming

COMPACT_DEFAULT_STRATEGY = "memento"


def build_compact_payload(raw: JsonValue) -> JsonObject:
    """构建手动上下文压缩请求载荷。"""
    data = raw if isinstance(raw, dict) else {}

    return {
        "cid": str(data.get("cid") or "").strip(),
        "sid": str(data.get("sid") or "").strip(),
        "llm_conf": request_llm_conf(data.get("llm_conf")),
        "strategy": str(data.get("strategy") or COMPACT_DEFAULT_STRATEGY).strip() or COMPACT_DEFAULT_STRATEGY
    }


async def stream_compact_events(
    payload: JsonObject,
    *,
    timeout: float = 120.0
) -> typing.AsyncGenerator[ContextCompactionEvent, None]:
    """读取并校验同一手动压缩操作的 SSE，不自动重提压缩请求。"""
    operation: tuple[str, str] | None = None
    terminal: ContextCompactionEvent | None = None
    async with contextlib.aclosing(streaming(
        service_endpoints.endpoint("/compact"),
        headers=build_service_headers(),
        payload=payload,
        timeout=timeout,
    )) as events:
        async for raw in events:
            if not isinstance(raw, dict):
                raise ValueError("compact event must be an object")
            event = parse_compact_event(raw)
            if event.cid != payload["cid"] or event.sid != payload["sid"]:
                raise ValueError("compact event does not match the requested session")
            identity = (event.turn_id, event.item_id)
            if operation is not None and operation != identity:
                raise ValueError("compact event does not match the current operation")
            operation = identity
            if event.item_status in {"completed", "failed"}:
                terminal = event
                break
            yield event
    if terminal is None:
        raise httpx.RemoteProtocolError("Compaction stream ended before a terminal event")
    # 上层在终态处停止迭代，须在交付终态前释放 HTTP 流。
    yield terminal


if __name__ == '__main__':
    pass
