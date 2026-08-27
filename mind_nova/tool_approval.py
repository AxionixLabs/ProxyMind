# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
from dataclasses import dataclass

ToolApprovalDecision: typing.TypeAlias = typing.Literal[
    "accept",
    "acceptForSession",
    "acceptWithExecpolicyAmendment",
    "decline",
    "cancel",
]

ToolApprovalStatus: typing.TypeAlias = typing.Literal[
    "approved",
    "declined",
    "cancelled",
]

ToolApprovalTurnStatus: typing.TypeAlias = typing.Literal[
    "active",
    "interrupting",
]

ToolApprovalSnapshotStatus: typing.TypeAlias = typing.Literal[
    "pending",
    "resolved",
    "expired",
    "cancelled",
]

ToolLifecycleStatus: typing.TypeAlias = typing.Literal[
    "completed",
    "failed",
    "declined",
    "cancelled",
]

TOOL_APPROVAL_DECISIONS: frozenset[ToolApprovalDecision] = frozenset[
    ToolApprovalDecision
]({
    "accept",
    "acceptForSession",
    "acceptWithExecpolicyAmendment",
    "decline",
    "cancel",
})

TOOL_APPROVAL_ACCEPT_DECISIONS: frozenset[ToolApprovalDecision] = frozenset[
    ToolApprovalDecision
]({
    "accept",
    "acceptForSession",
    "acceptWithExecpolicyAmendment",
})

TOOL_APPROVAL_STATUSES: frozenset[ToolApprovalStatus] = frozenset[
    ToolApprovalStatus
]({
    "approved",
    "declined",
    "cancelled",
})

TOOL_APPROVAL_TURN_STATUSES: frozenset[ToolApprovalTurnStatus] = frozenset[
    ToolApprovalTurnStatus
]({
    "active",
    "interrupting",
})

TOOL_LIFECYCLE_STATUSES: frozenset[ToolLifecycleStatus] = frozenset[
    ToolLifecycleStatus
]({
    "completed",
    "failed",
    "declined",
    "cancelled",
})


@dataclass(frozen=True, slots=True)
class ToolApprovalAck(object):
    """描述服务端确认后的审批与轮次状态。"""
    request_id: str
    turn_id: str
    approval_id: str
    call_id: str
    decision: ToolApprovalDecision
    tool_status: ToolApprovalStatus
    turn_status: ToolApprovalTurnStatus


@dataclass(frozen=True, slots=True)
class ToolApprovalSnapshotItem(object):
    """描述恢复快照中的单项审批记录。"""
    approval_id: str
    turn_id: str
    call_id: str
    name: str
    arguments: dict[str, typing.Any]
    approval: dict[str, typing.Any]
    status: ToolApprovalSnapshotStatus
    decision: str
    execpolicy_amendment_id: str
    reason: str
    additional_context: tuple[str, ...]
    ack: dict[str, typing.Any] | None
    expires_at: float
    resolved_at: float | None
    created_at: float
    updated_at: float


@dataclass(frozen=True, slots=True)
class ToolApprovalSnapshot(object):
    """描述指定逻辑轮次的审批恢复快照。"""
    cid: str
    sid: str
    turn_id: str
    turn_status: str
    turn_settled: bool
    last_event_seq: int
    approvals: tuple[ToolApprovalSnapshotItem, ...]


if __name__ == '__main__':
    pass
