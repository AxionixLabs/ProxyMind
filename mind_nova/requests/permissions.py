# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing

SandboxMode = typing.Literal[
    "read-only",
    "workspace-write",
    "danger-full-access",
]

ApprovalPolicy = typing.Literal[
    "untrusted",
    "on-request",
    "never",
]

ApprovalReviewer = typing.Literal[
    "user",
    "auto_review",
]

SANDBOX_MODES: tuple[SandboxMode, ...] = (
    "read-only",
    "workspace-write",
    "danger-full-access",
)

APPROVAL_POLICIES: tuple[ApprovalPolicy, ...] = (
    "untrusted",
    "on-request",
    "never",
)

APPROVAL_REVIEWERS: tuple[ApprovalReviewer, ...] = (
    "user",
    "auto_review",
)


def normalize_sandbox_mode(value: typing.Any) -> SandboxMode:
    """规范化请求协议使用的沙箱模式。"""
    normalized = str(value or "").strip().lower()
    if normalized in SANDBOX_MODES:
        return typing.cast(SandboxMode, normalized)
    raise ValueError(f"invalid sandbox mode: {value}")


def normalize_approval_policy(value: typing.Any) -> ApprovalPolicy:
    """规范化请求协议使用的审批策略。"""
    normalized = str(value or "").strip().lower()
    if normalized in APPROVAL_POLICIES:
        return typing.cast(ApprovalPolicy, normalized)
    raise ValueError(f"invalid approval policy: {value}")


def normalize_approval_reviewer(value: typing.Any) -> ApprovalReviewer:
    """规范化审批裁决方。"""
    normalized = str(value or "user").strip().lower()
    if normalized == "guardian_subagent":
        normalized = "auto_review"
    if normalized in APPROVAL_REVIEWERS:
        return typing.cast(ApprovalReviewer, normalized)
    raise ValueError(f"invalid approval reviewer: {value}")


def permission_payload(value: typing.Any) -> dict[str, str]:
    """把权限设置转换为请求协议字段。"""
    if isinstance(value, dict):
        sandbox_mode       = value.get("sandbox_mode")
        approval_policy    = value.get("approval_policy")
        approvals_reviewer = value.get("approvals_reviewer")
    else:
        sandbox_mode       = getattr(value, "sandbox_mode", None)
        approval_policy    = getattr(value, "approval_policy", None)
        approvals_reviewer = getattr(value, "approvals_reviewer", None)

    return {
        "sandbox_mode": normalize_sandbox_mode(sandbox_mode),
        "approval_policy": normalize_approval_policy(approval_policy),
        "approvals_reviewer": normalize_approval_reviewer(approvals_reviewer),
    }


if __name__ == '__main__':
    pass
