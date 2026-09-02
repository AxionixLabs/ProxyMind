# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

from .models import (
    ApprovalAction,
    ApprovalActionKind,
    ApprovalDecision,
    ApprovalDecisionKind,
)


_ACTION_DECISIONS: dict[
    ApprovalActionKind,
    frozenset[ApprovalDecisionKind],
] = {
    ApprovalActionKind.COMMAND: frozenset({
        ApprovalDecisionKind.ALLOW_ONCE,
        ApprovalDecisionKind.ALLOW_FOR_SESSION,
        ApprovalDecisionKind.APPLY_AMENDMENT,
        ApprovalDecisionKind.DECLINE,
        ApprovalDecisionKind.CANCEL,
    }),
    ApprovalActionKind.PATCH: frozenset({
        ApprovalDecisionKind.ALLOW_ONCE,
        ApprovalDecisionKind.ALLOW_FOR_SESSION,
        ApprovalDecisionKind.DECLINE,
        ApprovalDecisionKind.CANCEL,
    }),
    ApprovalActionKind.PERMISSION: frozenset({
        ApprovalDecisionKind.GRANT_FOR_RUN,
        ApprovalDecisionKind.DECLINE,
        ApprovalDecisionKind.CANCEL,
    }),
    ApprovalActionKind.MCP: frozenset({
        ApprovalDecisionKind.ALLOW_ONCE,
        ApprovalDecisionKind.ALLOW_FOR_SESSION,
        ApprovalDecisionKind.APPLY_AMENDMENT,
        ApprovalDecisionKind.DECLINE,
        ApprovalDecisionKind.CANCEL,
    }),
    ApprovalActionKind.NETWORK: frozenset({
        ApprovalDecisionKind.ALLOW_ONCE,
        ApprovalDecisionKind.ALLOW_FOR_SESSION,
        ApprovalDecisionKind.APPLY_AMENDMENT,
        ApprovalDecisionKind.DECLINE,
        ApprovalDecisionKind.CANCEL,
    }),
}

_UNIVERSAL_TERMINAL_DECISIONS = frozenset({
    ApprovalDecisionKind.TIMEOUT,
    ApprovalDecisionKind.UNAVAILABLE,
    ApprovalDecisionKind.ABANDONED,
})


def allowed_decisions(
    action_kind: ApprovalActionKind,
) -> frozenset[ApprovalDecisionKind]:
    """返回动作类别允许的决定集合。"""
    try:
        return _ACTION_DECISIONS[action_kind]
    except KeyError as error:
        raise ValueError(f"unsupported approval action kind: {action_kind}") from error


def validate_decision_kind(
    action_kind: ApprovalActionKind,
    decision_kind: ApprovalDecisionKind,
) -> None:
    """校验动作类别允许的决定类别。"""
    valid = allowed_decisions(action_kind)
    if (
        decision_kind not in valid
        and decision_kind not in _UNIVERSAL_TERMINAL_DECISIONS
    ):
        raise ValueError(
            f"decision {decision_kind.value} is not valid for {action_kind.value}"
        )


def validate_decision(
    action: ApprovalAction,
    decision: ApprovalDecision,
) -> None:
    """校验决定与动作类别、动作指纹和 amendment 的一致性。"""
    if decision.action_fingerprint != action.fingerprint:
        raise ValueError("approval decision does not match action fingerprint")

    validate_decision_kind(action.kind, decision.kind)

    if decision.amendment is not None:
        if decision.amendment.action_fingerprint != action.fingerprint:
            raise ValueError("approval amendment does not match action fingerprint")


def is_terminal_decision(decision: ApprovalDecision) -> bool:
    """判断决定是否结束等待而不产生授权。"""
    return decision.kind in {
        ApprovalDecisionKind.DECLINE,
        ApprovalDecisionKind.CANCEL,
        ApprovalDecisionKind.TIMEOUT,
        ApprovalDecisionKind.UNAVAILABLE,
        ApprovalDecisionKind.ABANDONED,
    }


if __name__ == '__main__':
    pass
