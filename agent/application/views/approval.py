# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing
from dataclasses import dataclass

from agent.application.approvals.models import ApprovalDecisionValue

ApprovalDecision: typing.TypeAlias = ApprovalDecisionValue

ApprovalState: typing.TypeAlias = typing.Literal[
    "approved",
    "denied",
    "cancelled",
]
ApprovalSource: typing.TypeAlias = typing.Literal[
    "user",
    "hook",
    "policy",
    "auto_review",
]
ApprovalReviewActionKind: typing.TypeAlias = typing.Literal[
    "command",
    "write_stdin",
    "apply_patch",
    "network_access",
    "request_permissions",
    "mcp_tool_call",
]


@dataclass(frozen=True, slots=True)
class ApprovalView:
    """描述工具审批结果的展示数据。"""

    approval: dict[str, typing.Any]
    decision: ApprovalDecision
    state: ApprovalState
    source: ApprovalSource = "user"


ApprovalReviewTerminalStatus: typing.TypeAlias = typing.Literal[
    "approved",
    "denied",
    "timed_out",
    "aborted",
]

ApprovalReviewRiskLevel: typing.TypeAlias = typing.Literal[
    "low",
    "medium",
    "high",
    "critical",
]

ApprovalReviewUserAuthorization: typing.TypeAlias = typing.Literal[
    "unknown",
    "low",
    "medium",
    "high",
]


@dataclass(frozen=True, slots=True, kw_only=True)
class ApprovalReviewView:
    """描述自动审批评审终态的跨前端展示数据。"""

    review_id: str
    approval_id: str
    call_id: str
    action_kind: ApprovalReviewActionKind
    action_summary: str
    status: ApprovalReviewTerminalStatus
    risk_level: ApprovalReviewRiskLevel | None = None
    user_authorization: ApprovalReviewUserAuthorization | None = None
    rationale: str | None = None

    def __post_init__(self) -> None:
        """校验评审终态展示不丢失身份或解释。"""
        for field_name in (
            "review_id",
            "approval_id",
            "call_id",
            "action_summary",
        ):
            value = getattr(self, field_name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"approval review {field_name} is required")
            object.__setattr__(self, field_name, value.strip())
        if self.action_kind not in {
            "command",
            "write_stdin",
            "apply_patch",
            "network_access",
            "request_permissions",
            "mcp_tool_call",
        }:
            raise ValueError("approval review action_kind is invalid")
        if self.status not in {
            "approved",
            "denied",
            "timed_out",
            "aborted",
        }:
            raise ValueError("approval review status is invalid")
        if self.risk_level not in {None, "low", "medium", "high", "critical"}:
            raise ValueError("approval review risk_level is invalid")
        if self.user_authorization not in {
            None,
            "unknown",
            "low",
            "medium",
            "high",
        }:
            raise ValueError("approval review user_authorization is invalid")
        if self.rationale is not None:
            rationale = self.rationale.strip()
            if not rationale:
                raise ValueError("approval review rationale cannot be empty")
            object.__setattr__(self, "rationale", rationale)
        if self.status in {"approved", "denied"}:
            if (
                self.risk_level is None
                or self.user_authorization is None
                or self.rationale is None
            ):
                raise ValueError("approval review decision details are required")
        elif self.status == "timed_out":
            if (
                self.risk_level is not None
                or self.user_authorization is not None
                or self.rationale is None
            ):
                raise ValueError("approval review timeout details are invalid")
        elif (
            self.risk_level is not None
            or self.user_authorization is not None
            or self.rationale is not None
        ):
            raise ValueError("aborted approval review cannot contain a decision")


if __name__ == '__main__':
    pass
