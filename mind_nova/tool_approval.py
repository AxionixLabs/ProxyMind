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
    turn_id: str
    approval_id: str
    call_id: str
    decision: ToolApprovalDecision
    tool_status: str
    turn_status: str


if __name__ == '__main__':
    pass
