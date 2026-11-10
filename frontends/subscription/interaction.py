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


def _approval_snapshot(request: ApprovalRequest, *, compact: bool = False) -> dict[str, JsonValue] | None:
    """序列化脱敏展示资料，并为已处理记录保留有界摘要。"""
    presentation = request.presentation
    details = json.dumps(dataclasses.asdict(presentation), ensure_ascii=False, default=str)
    if not compact and (len(details.encode("utf-8")) > 100000
                        or len(presentation.context.prompt) > 10000 or len(presentation.summary) > 10000):
        return None
    return {
        "approval_id": request.key.request_id, "tool": request.key.tool,
        "kind": request.key.kind,
        "prompt": presentation.context.prompt[:256] if compact else presentation.context.prompt,
        "summary": presentation.summary[:512] if compact else presentation.summary,
        "details": details[:1024] + "…" if compact and len(details) > 1024 else details,
        "decisions": list(request.decisions),
    }


def interaction_payload(call_id: str, control: ApprovalControl | None, *,
                        since: float = 0, clear: bool = False) -> dict[str, JsonValue]:
    """将已脱敏的审批展示对象投影为浏览器卡片。"""
    if control is None:
        return {"call_id": call_id, "approval": None, "local_required": False, "resolved": []}
    snapshot = control.snapshot
    resolved: list[JsonValue] = []
    for record in snapshot.resolved:
        if record.outcome.resolved_at >= since:
            resolved.append({"approval": _approval_snapshot(record.request, compact=True),
                             "decision": record.outcome.decision, "source": record.outcome.source,
                             "resolved_at": record.outcome.resolved_at})
    current = None if clear else snapshot.current
    if not isinstance(current, ApprovalRequest):
        return {"call_id": call_id, "revision": snapshot.revision, "approval": None,
                "local_required": current is not None, "resolved": resolved}
    approval = _approval_snapshot(current)
    return {
        "call_id": call_id, "revision": snapshot.revision, "local_required": approval is None,
        "approval": approval, "resolved": resolved,
    }


if __name__ == '__main__':
    pass
