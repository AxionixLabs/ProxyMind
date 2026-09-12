# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import contextlib
import typing

import httpx

from agent.application.turns.compact_result import CompactEvent
from agent.protocol.context_usage import ContextUsageRecord
from protocol.client.compact import (
    build_compact_payload,
    stream_compact_events,
)
from protocol.schema.json_value import JsonObject
from protocol.schema.stream_events import ContextUsageUpdatedEvent
from .context_usage import context_usage_record

__all__ = ("ProtocolCompactionClient",)


class ProtocolCompactionClient:
    """把远端压缩 wire 事件转换为 Harness 可消费的中立事件。"""

    async def stream(
        self,
        *,
        cid: str,
        sid: str,
        pref_config: JsonObject,
    ) -> typing.AsyncGenerator[CompactEvent | ContextUsageRecord, None]:
        """提交 memento 压缩并归一化受支持的进度与终态。"""
        payload = build_compact_payload({
            "cid": cid,
            "sid": sid,
            "llm_conf": pref_config,
            "strategy": "memento",
        })
        try:
            async with contextlib.aclosing(stream_compact_events(payload)) as events:
                async for event in events:
                    if isinstance(event, ContextUsageUpdatedEvent):
                        yield context_usage_record(event)
                        continue
                    if event.item_status == "in_progress":
                        yield CompactEvent(status="started", message="Context compacting...")
                    elif event.item_status == "completed":
                        yield CompactEvent(
                            status="completed",
                            message="Context compacted.",
                            before_items=event.before_items,
                            after_items=event.after_items,
                        )
                        return
                    else:
                        yield CompactEvent(
                            status="failed",
                            message=_compact_failure_message(event.error_type or ""),
                        )
                        return
        except httpx.HTTPStatusError as error:
            yield CompactEvent(
                status="failed",
                message=_compact_failure_message(status_code=error.response.status_code),
            )
        except httpx.HTTPError:
            yield CompactEvent(
                status="failed",
                message="Context compaction failed. Please try again.",
            )


def _compact_failure_message(error_type: str = "", *, status_code: int = 0) -> str:
    """把服务端错误类别或 HTTP 状态转换为本地压缩提示。"""
    if error_type == "empty_history" or status_code == 404:
        return "There is no conversation history to compact."
    if error_type == "not_compactable":
        return "A tool call is still running. Try again after it finishes."
    if error_type == "cas_conflict":
        return "Conversation changed while compacting. Please try again."
    if error_type == "persist_failed":
        return "Failed to save the compacted context. Please try again."
    if status_code == 409:
        return "Conversation is busy or changed. Try /compact again after the current operation finishes."
    return "Context compaction failed. Please try again."


if __name__ == '__main__':
    pass
