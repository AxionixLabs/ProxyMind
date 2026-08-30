# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
from dataclasses import dataclass

ToolApprovalKind: typing.TypeAlias = typing.Literal[
    "command",
    "write_stdin",
    "apply_patch",
    "network_access",
    "request_permissions",
    "mcp_tool_call",
]

ToolApprovalCommandDecision: typing.TypeAlias = typing.Literal[
    "accept",
    "acceptForSession",
    "acceptWithExecpolicyAmendment",
    "decline",
    "cancel",
]

ToolApprovalWriteStdinDecision: typing.TypeAlias = typing.Literal[
    "accept",
    "acceptForSession",
    "decline",
    "cancel",
]

ToolApprovalPatchDecision: typing.TypeAlias = typing.Literal[
    "accept",
    "acceptForSession",
    "decline",
    "cancel",
]

ToolApprovalNetworkDecision: typing.TypeAlias = typing.Literal[
    "accept",
    "acceptForSession",
    "applyNetworkPolicyAmendment",
    "decline",
    "cancel",
]

ToolApprovalPermissionsDecision: typing.TypeAlias = typing.Literal[
    "grantForTurn",
    "grantForTurnWithStrictAutoReview",
    "grantForSession",
    "decline",
    "cancel",
]

ToolApprovalMcpDecision: typing.TypeAlias = typing.Literal[
    "accept",
    "decline",
    "cancel",
]

ToolApprovalDecision: typing.TypeAlias = typing.Literal[
    "accept",
    "acceptForSession",
    "acceptWithExecpolicyAmendment",
    "applyNetworkPolicyAmendment",
    "grantForTurn",
    "grantForTurnWithStrictAutoReview",
    "grantForSession",
    "decline",
    "cancel",
]

ToolApprovalNetworkProtocol: typing.TypeAlias = typing.Literal[
    "http", "https", "socks5_tcp", "socks5_udp"
]

ToolApprovalNetworkPolicyAction: typing.TypeAlias = typing.Literal[
    "allow", "deny"
]

ToolApprovalPermissionScope: typing.TypeAlias = typing.Literal[
    "turn", "session"
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
    "applyNetworkPolicyAmendment",
    "grantForTurn",
    "grantForTurnWithStrictAutoReview",
    "grantForSession",
    "decline",
    "cancel",
})

TOOL_APPROVAL_ACCEPT_DECISIONS: frozenset[ToolApprovalDecision] = frozenset[
    ToolApprovalDecision
]({
    "accept",
    "acceptForSession",
    "acceptWithExecpolicyAmendment",
    "applyNetworkPolicyAmendment",
    "grantForTurn",
    "grantForTurnWithStrictAutoReview",
    "grantForSession",
})

TOOL_APPROVAL_DECISIONS_BY_KIND: dict[ToolApprovalKind, frozenset[str]] = {
    "command": frozenset({
        "accept", "acceptForSession", "acceptWithExecpolicyAmendment",
        "decline", "cancel",
    }),
    "write_stdin": frozenset({
        "accept", "acceptForSession", "decline", "cancel",
    }),
    "apply_patch": frozenset({
        "accept", "acceptForSession", "decline", "cancel",
    }),
    "network_access": frozenset({
        "accept", "acceptForSession", "applyNetworkPolicyAmendment",
        "decline", "cancel",
    }),
    "request_permissions": frozenset({
        "grantForTurn", "grantForTurnWithStrictAutoReview", "grantForSession",
        "decline", "cancel",
    }),
    "mcp_tool_call": frozenset({"accept", "decline", "cancel"}),
}

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
    kind: ToolApprovalKind = "command"
    additional_context: tuple[str, ...] = ()
    reason: str = ""
    scope: ToolApprovalPermissionScope | None = None
    permissions: dict[str, typing.Any] | None = None
    strict_auto_review: bool | None = None
    target: str | None = None
    host: str | None = None
    protocol: ToolApprovalNetworkProtocol | None = None
    port: int | None = None
    network_policy_amendment: dict[str, str] | None = None
    server: str | None = None
    tool_name: str | None = None
    arguments: typing.Any = None
    mcp_request_id: str | None = None


@dataclass(frozen=True, slots=True)
class ToolApprovalSnapshotItem(object):
    """描述恢复快照中的单项审批记录。"""
    approval_id: str
    turn_id: str
    call_id: str
    kind: ToolApprovalKind
    approval: dict[str, typing.Any]
    status: ToolApprovalSnapshotStatus
    ack: dict[str, typing.Any] | None

    @property
    def decision(self) -> str:
        """返回终态审批记录中的决定。"""
        return str(self.ack.get("decision") or "") if self.ack else ""


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
