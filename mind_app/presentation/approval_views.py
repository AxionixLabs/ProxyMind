# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
from mind_nova.tool_approval import (
    TOOL_APPROVAL_ACCEPT_DECISIONS,
    TOOL_APPROVAL_DECISIONS,
)
from .models import (
    ApprovalDecision,
    ApprovalSource,
    ApprovalState,
    ApprovalView
)


def build_approval_view(
    approval: typing.Any,
    *,
    decision: str,
    source: ApprovalSource = "user"
) -> ApprovalView:
    """构建工具审批结果的结构化展示数据。"""
    normalized_approval = dict(approval) if isinstance(approval, dict) else {}
    normalized_decision = _approval_decision(decision)

    return ApprovalView(
        approval=normalized_approval,
        decision=normalized_decision,
        state=_approval_state(normalized_decision),
        source=source,
    )


def _approval_decision(decision: str) -> ApprovalDecision:
    """归一化工具审批结果。"""
    if decision == "expired" or decision in TOOL_APPROVAL_DECISIONS:
        return typing.cast(ApprovalDecision, decision)

    return "decline"


def _approval_state(decision: ApprovalDecision) -> ApprovalState:
    """返回工具审批结果对应的展示状态。"""
    if decision == "expired":
        return "expired"
    if decision == "cancel":
        return "cancelled"
    if decision in TOOL_APPROVAL_ACCEPT_DECISIONS:
        return "approved"

    return "denied"


if __name__ == '__main__':
    pass
