# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import httpx
import typing
from engine.channel import Channel
from mind_nova.services import service_endpoints


async def post_stream_event(
    cid: str,
    sid: str,
    event: dict[str, typing.Any],
    *,
    timeout: float = 30.0
) -> None:
    """事件上报：把一条事件写入服务端缓存并广播给 SSE 订阅者。"""
    headers = Channel.make_headers()
    payload = {
        "cid"   : cid,
        "sid"   : sid,
        "event" : event
    }
    async with httpx.AsyncClient(timeout=timeout) as client:
        r = await client.post(service_endpoints.endpoint("/events-ingest"), headers=headers, json=payload)
        r.raise_for_status()


async def open_report_session(
    cid: str,
    sid: str,
    *,
    proto: str | None = None,
    timeout: float = 10.0
) -> dict[str, typing.Any]:
    """打开服务端报告会话，返回 report_url / report_id / stream_url / replay_url。"""
    headers = Channel.make_headers()
    payload: dict[str, typing.Any] = {
        "cid"  : cid,
        "sid"  : sid
    }
    if isinstance(proto, str) and proto.strip():
        payload["proto"] = proto.strip()

    async with httpx.AsyncClient(timeout=timeout) as client:
        r = await client.post(service_endpoints.endpoint("/reports/open"), headers=headers, json=payload)
        r.raise_for_status()
        body = r.json()

    if not isinstance(body, dict):
        raise RuntimeError("reports/open returned non-object payload")
    if not body.get("ok"):
        raise RuntimeError(f"reports/open rejected payload: {body}")

    data = body.get("data")
    if not isinstance(data, dict):
        raise RuntimeError("reports/open missing data object")

    return data


if __name__ == '__main__':
    pass
