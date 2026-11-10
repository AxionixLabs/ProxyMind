# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import dataclasses
import json
import typing

from agent.application.approvals.models import (
    ApprovalQueueSnapshot,
    ApprovalDecisionSource,
    ApprovalRequest,
)
from protocol.schema.json_value import JsonValue


class ApprovalControl(typing.Protocol):
    """提供当前审批的只读快照和外部用户决定入口。"""

    @property
    def snapshot(self) -> ApprovalQueueSnapshot:
        """返回当前审批队列快照。"""
        ...

    async def resolve(self, request: str, decision: str, *, source: ApprovalDecisionSource) -> bool:
        """经已有协调器收束指定审批。"""
        ...


def interaction_payload(call_id: str, control: ApprovalControl | None) -> dict[str, JsonValue]:
    """将已脱敏的审批展示对象投影为浏览器卡片。"""
    if control is None:
        return {"call_id": call_id, "approval": None, "local_required": False}
    snapshot = control.snapshot
    current = snapshot.current
    if not isinstance(current, ApprovalRequest):
        return {"call_id": call_id, "revision": snapshot.revision, "approval": None,
                "local_required": current is not None}
    presentation = current.presentation
    details = json.dumps(dataclasses.asdict(presentation), ensure_ascii=False, default=str)
    if (len(details) > 100000 or len(presentation.context.prompt) > 10000
            or len(presentation.summary) > 10000):
        return {"call_id": call_id, "revision": snapshot.revision, "approval": None,
                "local_required": True}
    return {
        "call_id": call_id, "revision": snapshot.revision, "local_required": False,
        "approval": {
            "approval_id": current.key.request_id, "tool": current.key.tool,
            "kind": current.key.kind, "prompt": presentation.context.prompt,
            "summary": presentation.summary,
            "details": details,
            "decisions": list(current.decisions),
        },
    }


if __name__ == '__main__':
    pass
