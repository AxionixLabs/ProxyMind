# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import httpx

from agent.ports.conversation import ContextUsageRecoveryError
from agent.protocol.context_usage import ContextUsageRecord
from protocol.client.context_usage import recover_context_usage
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


class ProtocolContextUsageRecovery:
    """把既有回放响应映射为根会话用量事实，关闭请求且不持有会话状态。"""

    async def load(self, cid: str, sid: str) -> ContextUsageRecord | None:
        """归一化恢复失败，避免将带查看令牌的 HTTP 地址泄露到展示层。"""
        try:
            event = await recover_context_usage(cid, sid)
            return context_usage_record(event) if event is not None else None
        except (httpx.HTTPError, TimeoutError, ValueError, TypeError, RuntimeError):
            raise ContextUsageRecoveryError("context usage recovery failed") from None


if __name__ == '__main__':
    pass
