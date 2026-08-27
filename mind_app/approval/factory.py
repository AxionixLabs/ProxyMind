# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import copy
import uuid
import typing
from collections.abc import Mapping
from .models import (
    ApprovalPayload,
    ApprovalRequest,
    ApprovalRequestKey
)
from .policy import approval_decisions
from .presentation import (
    approval_request_kind,
    build_approval_presentation
)


def build_approval_request(
    payload: Mapping[str, typing.Any] | ApprovalPayload,
) -> ApprovalRequest:
    """把一次协议或本地审批载荷规范化为应用层请求。"""
    if isinstance(payload, ApprovalPayload):
        normalized = payload.as_dict()
        kind = payload.kind
    elif isinstance(payload, Mapping):
        normalized = copy.deepcopy(dict(payload))
        kind = approval_request_kind(normalized)
    else:
        raise TypeError("approval payload must be a mapping or typed payload")

    normalized["kind"] = kind
    approval_id = _text(normalized.get("approval_id") or normalized.get("id"))
    call_id = _text(normalized.get("call_id"))
    request_id = _text(
        normalized.get("request_id")
        or normalized.get("requestId")
        or approval_id
        or call_id
    )
    if not request_id:
        request_id = f"local-approval-{uuid.uuid4().hex}"
    normalized["request_id"] = request_id

    tool = _text(normalized.get("tool")) or _default_tool(kind)
    normalized["tool"] = tool
    key = ApprovalRequestKey(
        request_id=request_id,
        approval_id=approval_id,
        call_id=call_id,
        tool=tool,
        kind=kind,
    )
    decisions = tuple(approval_decisions(normalized))
    request_payload = ApprovalPayload(kind=kind, values=normalized)
    return ApprovalRequest(
        key=key,
        payload=request_payload,
        presentation=build_approval_presentation(
            request_payload.as_dict(),
            key=key,
            kind=kind,
            decisions=decisions,
        ),
        decisions=decisions,
    )


def _default_tool(kind: str) -> str:
    """返回审批类别对应的内部工具名称。"""
    return {
        "command": "shell_command",
        "write_stdin": "write_stdin",
        "apply_patch": "apply_patch",
        "network_access": "exec_command",
        "request_permissions": "request_permissions",
        "mcp_tool_call": "mcp_tool_call",
    }.get(kind, "shell_command")


def _text(value: typing.Any) -> str:
    """读取协议中的文本标识。"""
    return str(value or "").strip()


if __name__ == "__main__":
    pass
