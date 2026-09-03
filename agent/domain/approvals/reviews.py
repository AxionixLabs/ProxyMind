# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import enum
from dataclasses import dataclass

from .models import (
    ActionFingerprint,
    ApprovalActionKind,
)


class ApprovalReviewConflict(ValueError):
    """表示自动审批评审事实与当前动作或既有事实冲突。"""


class ApprovalReviewStatus(enum.StrEnum):
    """列出自动审批评审的完整生命周期状态。"""

    IN_PROGRESS = "in_progress"
    APPROVED = "approved"
    DENIED = "denied"
    TIMED_OUT = "timed_out"
    ABORTED = "aborted"


class ApprovalReviewRiskLevel(enum.StrEnum):
    """列出自动审批评审可以报告的风险等级。"""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class ApprovalReviewUserAuthorization(enum.StrEnum):
    """列出评审判断的用户授权强度。"""

    UNKNOWN = "unknown"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


@dataclass(frozen=True, slots=True)
class ApprovalReviewIdentity:
    """标识独立评审及其对应的审批动作。"""

    session_id: str
    run_id: str
    review_id: str
    approval_id: str
    action_id: str
    action_kind: ApprovalActionKind

    def __post_init__(self) -> None:
        """校验评审身份和动作类别。"""
        for name in (
            "session_id",
            "run_id",
            "review_id",
            "approval_id",
            "action_id",
        ):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"approval review {name} is required")
            object.__setattr__(self, name, value.strip())
        if not isinstance(self.action_kind, ApprovalActionKind):
            raise TypeError("approval review action_kind is invalid")


@dataclass(frozen=True, slots=True)
class ApprovalReviewRecord:
    """保存可幂等归约的一次自动审批评审事实。"""

    identity: ApprovalReviewIdentity
    action_fingerprint: ActionFingerprint
    status: ApprovalReviewStatus
    event_seq: int
    presentation_epoch: int
    started_at_ms: int
    completed_at_ms: int | None = None
    risk_level: ApprovalReviewRiskLevel | None = None
    user_authorization: ApprovalReviewUserAuthorization | None = None
    rationale: str | None = None

    def __post_init__(self) -> None:
        """校验状态、时间、决定字段和生命周期组合。"""
        if not isinstance(self.identity, ApprovalReviewIdentity):
            raise TypeError("approval review identity is invalid")
        if not isinstance(self.action_fingerprint, ActionFingerprint):
            raise TypeError("approval review action fingerprint is invalid")
        if not isinstance(self.status, ApprovalReviewStatus):
            raise TypeError("approval review status is invalid")
        for name in ("event_seq", "presentation_epoch"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                raise ValueError(f"approval review {name} must be positive")
        if (
            isinstance(self.started_at_ms, bool)
            or not isinstance(self.started_at_ms, int)
            or self.started_at_ms < 0
        ):
            raise ValueError("approval review started_at_ms must be non-negative")
        if self.rationale is not None:
            rationale = self.rationale.strip()
            if not rationale:
                raise ValueError("approval review rationale cannot be empty")
            object.__setattr__(self, "rationale", rationale)

        if self.status is ApprovalReviewStatus.IN_PROGRESS:
            if any(item is not None for item in (
                self.completed_at_ms,
                self.risk_level,
                self.user_authorization,
                self.rationale,
            )):
                raise ValueError("in-progress approval review contains terminal data")
            return

        completed_at_ms = self.completed_at_ms
        if (
            isinstance(completed_at_ms, bool)
            or not isinstance(completed_at_ms, int)
            or completed_at_ms < self.started_at_ms
        ):
            raise ValueError("terminal approval review has invalid completion time")
        if self.status in {
            ApprovalReviewStatus.APPROVED,
            ApprovalReviewStatus.DENIED,
        }:
            if (
                not isinstance(self.risk_level, ApprovalReviewRiskLevel)
                or not isinstance(
                    self.user_authorization,
                    ApprovalReviewUserAuthorization,
                )
                or self.rationale is None
            ):
                raise ValueError("approval review decision fields are incomplete")
        elif self.status is ApprovalReviewStatus.TIMED_OUT:
            if (
                self.risk_level is not None
                or self.user_authorization is not None
                or self.rationale is None
            ):
                raise ValueError("timed-out approval review fields are invalid")
        elif self.risk_level is not None or self.user_authorization is not None:
            raise ValueError("aborted approval review cannot contain a risk decision")

    @property
    def terminal(self) -> bool:
        """返回评审是否已经形成终态。"""
        return self.status is not ApprovalReviewStatus.IN_PROGRESS


if __name__ == '__main__':
    pass
