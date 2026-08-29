# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

from mind_nova.requests.chat import (
    TurnEventStream,
    stream_chat
)
from agent.ports import (
    ApprovalSnapshotCallback,
    ReconnectStatusCallback,
)
from agent.protocol import ModelStreamRequest


class RemoteModelCapability:
    """把冻结的模型请求翻译到现有远端事件流传输。"""

    def stream(
        self,
        request: ModelStreamRequest,
        *,
        on_reconnect_status: ReconnectStatusCallback | None = None,
        on_approval_snapshot: ApprovalSnapshotCallback | None = None,
    ) -> TurnEventStream:
        """创建保留重连、审批恢复和事件水位语义的远端流。"""
        return stream_chat(
            request.pref_config_value(),
            request.message,
            request.tool_values(),
            attachments=request.attachment_values() or None,
            timeout=request.timeout,
            on_reconnect_status=on_reconnect_status,
            on_approval_snapshot=on_approval_snapshot,
            initial_event_seq=request.initial_event_seq,
            **request.option_values(),
        )


if __name__ == '__main__':
    pass
