# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

from protocol.client.session_replay import recover_session_context
from protocol.schema.stream_events import ContextUsageUpdatedEvent


async def recover_context_usage(cid: str, sid: str) -> ContextUsageUpdatedEvent | None:
    """读取完整权威用量快照，不修改事件确认游标或触发模型调用。"""
    return (await recover_session_context(cid, sid)).context_usage


if __name__ == '__main__':
    pass
