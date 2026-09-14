# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import contextlib
import typing
from dataclasses import replace

import httpx

from agent.application.turns.compact_result import (
    CompactEvent,
    compact_failure_message,
)
from agent.protocol.context_usage import ContextUsageRecord
from agent.ports.compaction import CompactionRecoveryError
from protocol.client.session_replay import recover_session_context
from protocol.client.compact import (
    build_compact_payload,
    stream_compact_events,
)
from protocol.schema.json_value import JsonObject
from protocol.schema.stream_events import (
    ContextCompactionEvent,
    ContextUsageUpdatedEvent,
)
from .context_usage import context_usage_record

__all__ = (
    "ProtocolCompactionClient",
    "ProtocolCompactionRecovery",
)


def compact_event(event: ContextCompactionEvent) -> CompactEvent:
    """把实时与回放压缩事件转换为相同的中立事实。"""
    status: typing.Literal["started", "completed", "failed"] = (
        "started" if event.item_status == "in_progress"
        else "completed" if event.item_status == "completed"
        else "failed"
    )
    return CompactEvent(
        status=status,
        message=(
            "Context compacting..." if status == "started"
            else "Context compacted." if status == "completed"
            else compact_failure_message(event.error_type or "")
        ),
        cid=event.cid,
        sid=event.sid,
        turn_id=event.turn_id,
        item_id=event.item_id,
        event_seq=event.event_seq,
        presentation_epoch=event.presentation_epoch,
        phase=event.phase,
        trigger=event.trigger,
        reason=event.reason,
        error_type=event.error_type,
        retryable=event.retryable,
        before_items=event.before_items,
        after_items=event.after_items,
        before_chars=event.before_chars,
        after_chars=event.after_chars,
        dropped_items=event.dropped_items,
        reduction_ratio=event.reduction_ratio,
        latency_ms=event.latency_ms,
        replacement_version=event.replacement_version,
    )


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
        observed: CompactEvent | None = None
        try:
            async with contextlib.aclosing(stream_compact_events(payload)) as events:
                async for event in events:
                    if isinstance(event, ContextUsageUpdatedEvent):
                        yield context_usage_record(event)
                        continue
                    observed = compact_event(event)
                    yield observed
                    if observed.status != "started":
                        return
        except httpx.HTTPStatusError as error:
            uncertain = error.response.status_code >= 500 or error.response.status_code == 408
            yield CompactEvent(
                status="unknown" if uncertain else "failed",
                message=(
                    "Lost contact with context compaction; remote outcome unknown." if uncertain
                    else compact_failure_message(status_code=error.response.status_code)
                ),
            )
        except httpx.HTTPError:
            yield replace(
                observed or CompactEvent(status="unknown"),
                status="unknown",
                message="Lost contact with context compaction; remote outcome unknown.",
            )


class ProtocolCompactionRecovery:
    """把会话回放映射为压缩事实，单次读取不持有会话状态。"""

    async def load(self, cid: str, sid: str, *, after_seq: int) -> tuple[CompactEvent, ...]:
        """读取完整分页，隔离可能包含报告令牌的传输错误。"""
        try:
            snapshot = await recover_session_context(cid, sid, after_seq=after_seq)
            return tuple(
                compact_event(event) for event in snapshot.compactions
                if event.turn_id == "" and event.trigger == "manual" and event.phase == "standalone"
            )
        except (httpx.HTTPError, TimeoutError, ValueError, TypeError, RuntimeError):
            raise CompactionRecoveryError("context compaction recovery failed") from None


if __name__ == '__main__':
    pass
