# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import asyncio

import httpx

from protocol.schema.stream_events import (
    ContextUsageUpdatedEvent,
    parse_stream_event,
)
from protocol.transport.endpoints import service_endpoints
from protocol.transport.reports import open_report_session


async def recover_context_usage(cid: str, sid: str) -> ContextUsageUpdatedEvent | None:
    """经既有报告授权读取完整回放快照，不修改事件确认游标或触发模型调用。"""
    async with asyncio.timeout(15):
        grant = await open_report_session(cid, sid, proto="mind.chat")
        token = grant.get("vt")
        if not isinstance(token, str) or not token:
            raise ValueError("context recovery requires a report view token")
        cursor = 0
        async with httpx.AsyncClient(timeout=10) as client:
            while True:
                response = await client.get(
                    service_endpoints.endpoint("/mind-replay"),
                    params={"cid": cid, "sid": sid, "vt": token, "after_seq": cursor, "limit": 5000},
                )
                response.raise_for_status()
                body = response.json()
                if not isinstance(body, dict) or body.get("ok") is not True:
                    raise ValueError("context recovery response is invalid")
                data = body.get("data")
                if not isinstance(data, dict) or "context_usage" not in data:
                    raise ValueError("context recovery snapshot is missing")
                gap = data.get("gap")
                if gap not in ("none", "retained_prefix"):
                    raise ValueError("context recovery has an invalid event gap")
                next_seq = data.get("next_seq")
                has_more = data.get("has_more")
                if isinstance(next_seq, bool) or not isinstance(next_seq, int) or next_seq < cursor:
                    raise ValueError("context recovery cursor is invalid")
                if not isinstance(has_more, bool):
                    raise ValueError("context recovery pagination is invalid")
                raw = data["context_usage"]
                event = None
                if raw is not None:
                    if not isinstance(raw, dict):
                        raise ValueError("context recovery snapshot is invalid")
                    event = parse_stream_event(raw)
                    if not isinstance(event, ContextUsageUpdatedEvent) or (event.cid, event.sid) != (cid, sid):
                        raise ValueError("context recovery snapshot identity does not match")
                if not has_more:
                    if event is not None and event.event_seq is not None and event.event_seq > next_seq:
                        raise ValueError("context recovery ended before snapshot watermark")
                    return event
                if next_seq <= cursor:
                    raise ValueError("context recovery did not advance")
                cursor = next_seq


if __name__ == '__main__':
    pass
