# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

from agent.application.views import ContextCompactionView
from protocol.schema.stream_events import ContextCompactionEvent


def build_context_compaction_view(
    event: ContextCompactionEvent,
) -> ContextCompactionView:
    """把权威压缩事件转换为不含摘要正文的共享展示数据。"""
    event_seq = event.event_seq
    if isinstance(event_seq, bool) or not isinstance(event_seq, int):
        raise ValueError("context compaction event_seq is required")
    if event.item_status not in {"in_progress", "completed", "failed"}:
        raise ValueError("context compaction item status is invalid")

    return ContextCompactionView(
        turn_id=event.turn_id,
        item_id=event.item_id,
        event_seq=event_seq,
        presentation_epoch=event.presentation_epoch,
        status=event.item_status,
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


if __name__ == '__main__':
    pass
