# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import copy
import time
import typing
from dataclasses import dataclass
from protocol.schema.tool_approval import (
    ToolApprovalDecision,
    ToolApprovalKind,
)

if typing.TYPE_CHECKING:
    from .presentation import ApprovalPresentation

ApprovalDecisionValue: typing.TypeAlias = ToolApprovalDecision

ApprovalDecisionSource = typing.Literal[
    "user",
    "policy",
    "auto_review",
]

ApprovalRequestKind: typing.TypeAlias = ToolApprovalKind

ApprovalResolutionReason = typing.Literal[
    "user",
    "policy",
    "external",
    "caller_cancelled",
    "batch_cancelled",
    "closed",
    "overloaded",
    "identity_conflict",
    "presentation_failed",
]


def normalize_approval_decision(value: object) -> ApprovalDecisionValue:
    """校验外部审批决定并返回精确的稳定枚举值。"""
    normalized = str(value or "").strip()
    if normalized == "accept":
        return "accept"
    if normalized == "acceptForSession":
        return "acceptForSession"
    if normalized == "acceptWithExecpolicyAmendment":
        return "acceptWithExecpolicyAmendment"
    if normalized == "applyNetworkPolicyAmendment":
        return "applyNetworkPolicyAmendment"
    if normalized == "grantForTurn":
        return "grantForTurn"
    if normalized == "grantForTurnWithStrictAutoReview":
        return "grantForTurnWithStrictAutoReview"
    if normalized == "grantForSession":
        return "grantForSession"
    if normalized == "decline":
        return "decline"
    if normalized == "cancel":
        return "cancel"
    raise ValueError(f"unsupported approval decision: {normalized}")


@dataclass(frozen=True, slots=True)
class ApprovalRequestKey(object):
    """稳定标识一个审批请求，供去重和外部解决使用。"""
    request_id: str
    approval_id: str
    call_id: str
    tool: str
    kind: ApprovalRequestKind


@dataclass(frozen=True, slots=True)
class ApprovalPayload(object):
    """保存一次审批请求的规范化类别和字段快照。"""
    kind: ApprovalRequestKind
    values: dict[str, typing.Any]

    def __post_init__(self) -> None:
        """复制载荷，避免队列状态受到调用方修改。"""
        object.__setattr__(self, "values", copy.deepcopy(self.values))

    def get(self, name: str, default: typing.Any = None) -> typing.Any:
        """读取规范化载荷中的字段。"""
        return self.values.get(name, default)

    def as_dict(self) -> dict[str, typing.Any]:
        """返回供展示或效果处理使用的独立字段副本。"""
        return copy.deepcopy(self.values)


@dataclass(frozen=True, slots=True)
class ApprovalRequest(object):
    """保存应用层审批队列使用的规范化请求。"""
    key: ApprovalRequestKey
    payload: ApprovalPayload
    presentation: "ApprovalPresentation"
    decisions: tuple[ApprovalDecisionValue, ...]


@dataclass(frozen=True, slots=True)
class ApprovalOutcome(object):
    """描述审批请求的终态、来源和收束原因。"""
    decision: ApprovalDecisionValue
    source: ApprovalDecisionSource
    reason: ApprovalResolutionReason
    resolved_at: float

    @classmethod
    def create(
        cls,
        decision: ApprovalDecisionValue,
        *,
        source: ApprovalDecisionSource,
        reason: ApprovalResolutionReason,
    ) -> "ApprovalOutcome":
        """使用当前时间建立审批终态。"""
        return cls(
            decision=decision,
            source=source,
            reason=reason,
            resolved_at=time.time(),
        )


@dataclass(frozen=True, slots=True)
class ApprovalQueueSnapshot(object):
    """提供审批队列的不可变可观察快照。"""
    current: ApprovalRequest | None
    pending: tuple[ApprovalRequestKey, ...]
    revision: int
    coordinator_id: str = ""
    closed: bool = False

    @property
    def pending_count(self) -> int:
        """返回当前请求之后等待展示的请求数。"""
        return len(self.pending)

    @property
    def unresolved_count(self) -> int:
        """返回 current 和 pending 中尚未收束的请求数。"""
        return (1 if self.current is not None else 0) + len(self.pending)


if __name__ == '__main__':
    pass
