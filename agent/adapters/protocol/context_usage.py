# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

from agent.protocol.context_usage import ContextUsageRecord
from protocol.schema.stream_events import ContextUsageUpdatedEvent


def context_usage_record(event: ContextUsageUpdatedEvent) -> ContextUsageRecord:
    """把严格 wire 快照转换为不依赖传输的本地事实。"""
    snapshot = event.snapshot
    if event.event_seq is None:
        raise ValueError("context usage requires a committed event sequence")
    return ContextUsageRecord(
        cid=event.cid,
        sid=event.sid,
        turn_id=event.turn_id,
        event_seq=event.event_seq,
        presentation_epoch=event.presentation_epoch,
        model_context_window=snapshot.model_context_window,
        last_total_tokens=(
            snapshot.last_token_usage.total_tokens
            if snapshot.last_token_usage is not None else None
        ),
        total_tokens=(
            snapshot.total_token_usage.total_tokens
            if snapshot.total_token_usage is not None else None
        ),
        usage_source=snapshot.usage_source,
        model=snapshot.model,
        route=snapshot.route,
    )


if __name__ == '__main__':
    pass
