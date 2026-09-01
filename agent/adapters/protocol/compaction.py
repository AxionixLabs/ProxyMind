# -*- coding: utf-8 -*-

import typing

from agent.application.turns.compact_result import CompactEvent
from protocol.client.compact import (
    build_compact_payload,
    stream_compact_events,
)


class ProtocolCompactionClient:
    """把远端压缩 wire 事件转换为 Harness 可消费的中立事件。"""

    async def stream(
        self,
        *,
        cid: str,
        sid: str,
        pref_config: dict[str, typing.Any],
    ) -> typing.AsyncIterator[CompactEvent]:
        """提交 memento 压缩并归一化受支持的进度与终态。"""
        payload = build_compact_payload({
            "cid": cid,
            "sid": sid,
            "llm_conf": pref_config,
            "strategy": "memento",
        })
        async for event in stream_compact_events(payload):
            event_type = str(event.get("type") or "")
            message = str(event.get("message") or "").strip()
            if event_type == "conversation.compact.started":
                yield CompactEvent(status="started", message=message)
                continue
            if event_type == "conversation.compact.failed":
                yield CompactEvent(
                    status="failed",
                    message=message,
                    summary=str(
                        event.get("summary") or message or ""
                    ).strip(),
                )
                continue
            if event_type == "conversation.compact":
                yield CompactEvent(
                    status="completed",
                    message=message,
                    summary=str(
                        event.get("summary") or message or "Context compacted."
                    ).strip(),
                    before_items=_optional_int(event.get("before_items")),
                    after_items=_optional_int(event.get("after_items")),
                )


def _optional_int(value: typing.Any) -> int | None:
    """将 wire 统计值规范化为可选整数。"""
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value


__all__ = ("ProtocolCompactionClient",)
