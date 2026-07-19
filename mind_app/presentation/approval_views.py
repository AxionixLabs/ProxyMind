# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
from .models import (
    ApprovalDecision,
    ApprovalState,
    ApprovalView
)


def build_approval_view(
    approval: typing.Any,
    *,
    decision: str,
) -> ApprovalView:
    """构建工具审批结果的结构化展示数据。"""
    normalized_approval = dict(approval) if isinstance(approval, dict) else {}
    normalized_decision = _approval_decision(decision)

    return ApprovalView(
        approval=normalized_approval,
        decision=normalized_decision,
        state=_approval_state(normalized_decision),
    )


def _approval_decision(decision: str) -> ApprovalDecision:
    """归一化工具审批结果。"""
    if decision in {"accept", "acceptForSession", "expired"}:
        return typing.cast(ApprovalDecision, decision)

    return "decline"


def _approval_state(decision: ApprovalDecision) -> ApprovalState:
    """返回工具审批结果对应的展示状态。"""
    if decision == "expired":
        return "expired"
    if decision in {"accept", "acceptForSession"}:
        return "approved"

    return "denied"


if __name__ == '__main__':
    pass
