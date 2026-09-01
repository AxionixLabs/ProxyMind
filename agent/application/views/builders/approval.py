# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
from protocol.schema.tool_approval import (
    TOOL_APPROVAL_ACCEPT_DECISIONS,
)
from agent.application.views import (
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
    if decision == "accept":
        return decision
    if decision == "acceptForSession":
        return decision
    if decision == "acceptWithExecpolicyAmendment":
        return decision
    if decision == "applyNetworkPolicyAmendment":
        return decision
    if decision == "grantForTurn":
        return decision
    if decision == "grantForTurnWithStrictAutoReview":
        return decision
    if decision == "grantForSession":
        return decision
    if decision == "decline":
        return decision
    if decision == "cancel":
        return decision

    return "decline"


def _approval_state(decision: ApprovalDecision) -> ApprovalState:
    """返回工具审批结果对应的展示状态。"""
    if decision == "cancel":
        return "cancelled"
    if decision in TOOL_APPROVAL_ACCEPT_DECISIONS:
        return "approved"

    return "denied"


if __name__ == '__main__':
    pass
