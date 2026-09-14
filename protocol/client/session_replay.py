# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import asyncio
from dataclasses import dataclass

import httpx

from protocol.schema.stream_events import (
    ContextCompactionEvent,
    ContextUsageUpdatedEvent,
    parse_stream_event,
)
from protocol.transport.endpoints import service_endpoints
from protocol.transport.reports import open_report_session


@dataclass(frozen=True, slots=True)
class SessionContextSnapshot:
    """保存有限回放读取的会话上下文事实；读取水位不代表聊天消费确认。"""

    context_usage: ContextUsageUpdatedEvent | None
    compactions: tuple[ContextCompactionEvent, ...]


async def recover_session_context(cid: str, sid: str, *, after_seq: int = 0) -> SessionContextSnapshot:
    """通过报告授权读取完整分页，在单次超时及取消范围内关闭传输。"""
    async with asyncio.timeout(15):
        grant = await open_report_session(cid, sid, proto="mind.chat")
        token = grant.get("vt")
        if not isinstance(token, str) or not token:
            raise ValueError("context recovery requires a report view token")
        cursor = after_seq
        compactions: list[ContextCompactionEvent] = []
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
                if data.get("gap") not in ("none", "retained_prefix"):
                    raise ValueError("context recovery has an invalid event gap")
                next_seq, has_more = data.get("next_seq"), data.get("has_more")
                if isinstance(next_seq, bool) or not isinstance(next_seq, int) or next_seq < cursor:
                    raise ValueError("context recovery cursor is invalid")
                if not isinstance(has_more, bool):
                    raise ValueError("context recovery pagination is invalid")
                raw_events = data.get("events")
                if not isinstance(raw_events, list):
                    raise ValueError("context recovery events are missing")
                previous_seq = cursor
                for raw_event in raw_events:
                    if not isinstance(raw_event, dict):
                        raise ValueError("context recovery event is invalid")
                    event = parse_stream_event(raw_event)
                    if (event.cid, event.sid) != (cid, sid):
                        raise ValueError("context recovery event identity does not match")
                    seq = event.event_seq
                    if seq is None or not previous_seq < seq <= next_seq:
                        raise ValueError("context recovery event sequence is invalid")
                    previous_seq = seq
                    if isinstance(event, ContextCompactionEvent):
                        compactions.append(event)
                raw_usage = data["context_usage"]
                usage = None
                if raw_usage is not None:
                    if not isinstance(raw_usage, dict):
                        raise ValueError("context recovery snapshot is invalid")
                    parsed_usage = parse_stream_event(raw_usage)
                    if not isinstance(parsed_usage, ContextUsageUpdatedEvent) or (parsed_usage.cid, parsed_usage.sid) != (cid, sid):
                        raise ValueError("context recovery snapshot identity does not match")
                    usage = parsed_usage
                if not has_more:
                    if usage is not None and usage.event_seq is not None and usage.event_seq > next_seq:
                        raise ValueError("context recovery ended before snapshot watermark")
                    return SessionContextSnapshot(usage, tuple(compactions))
                if next_seq <= cursor:
                    raise ValueError("context recovery did not advance")
                cursor = next_seq


if __name__ == '__main__':
    pass
