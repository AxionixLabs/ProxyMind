# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing

from agent.application.views import (
    ApprovalDecision,
    ApprovalSource,
    ApprovalState,
    ApprovalReviewActionKind,
    ApprovalView,
    ApprovalReviewView,
)
from agent.application.approvals.summary import approval_review_action_summary
from agent.domain.approvals import (
    ApprovalReviewRecord,
    ApprovalReviewStatus,
)

_LOCAL_APPROVAL_ACCEPT_DECISIONS = frozenset({
    "accept",
    "acceptForSession",
    "acceptAndRemember",
    "acceptWithExecpolicyAmendment",
    "applyNetworkPolicyAmendment",
    "grantForTurn",
    "grantForTurnWithStrictAutoReview",
    "grantForSession",
})


def build_approval_view(
    approval: typing.Any,
    *,
    decision: str,
    source: ApprovalSource = "user"
) -> ApprovalView:
    """构建工具审批结果的结构化展示数据。"""
    if source == "auto_review":
        raise ValueError("automatic review must use ApprovalReviewView")
    normalized_approval = dict(approval) if isinstance(approval, dict) else {}
    normalized_decision = _approval_decision(decision)

    return ApprovalView(
        approval=normalized_approval,
        decision=normalized_decision,
        state=_approval_state(normalized_decision),
        source=source,
    )


def build_approval_review_view(
    review: ApprovalReviewRecord,
    *,
    action_kind: ApprovalReviewActionKind,
    action: typing.Mapping[str, typing.Any],
) -> ApprovalReviewView:
    """构建已完成自动审批评审的跨前端展示数据。"""
    if not isinstance(review, ApprovalReviewRecord) or not review.terminal:
        raise ValueError("terminal approval review is required")
    status = review.status
    if status is ApprovalReviewStatus.IN_PROGRESS:
        raise ValueError("terminal approval review is required")
    identity = review.identity
    return ApprovalReviewView(
        review_id=identity.review_id,
        approval_id=identity.approval_id,
        call_id=identity.action_id,
        action_kind=action_kind,
        action_summary=approval_review_action_summary(action_kind, action),
        status=status.value,
        risk_level=(
            review.risk_level.value
            if review.risk_level is not None
            else None
        ),
        user_authorization=(
            review.user_authorization.value
            if review.user_authorization is not None
            else None
        ),
        rationale=review.rationale,
    )


def _approval_decision(decision: str) -> ApprovalDecision:
    """归一化工具审批结果。"""
    if decision == "accept":
        return decision
    if decision == "acceptForSession":
        return decision
    if decision == "acceptAndRemember":
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
    if decision in _LOCAL_APPROVAL_ACCEPT_DECISIONS:
        return "approved"

    return "denied"


if __name__ == '__main__':
    pass
