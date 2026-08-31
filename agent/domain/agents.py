# -*- coding: utf-8 -*-

import time
import typing
from dataclasses import dataclass

from protocol.schema.identifiers import short_uid

AgentStatus = typing.Literal[
    "pending",
    "running",
    "completed",
    "failed",
    "interrupted",
    "interrupted_by_restart",
    "closed",
]
AgentResumeStatus = typing.Literal[
    "completed",
    "failed",
    "interrupted",
    "interrupted_by_restart",
]
AgentSubmissionKind = typing.Literal["initial", "followup"]

FINAL_AGENT_STATUSES = frozenset({
    "completed",
    "failed",
    "interrupted",
    "interrupted_by_restart",
    "closed",
})
RESTART_INTERRUPTION_ERROR = "agent execution interrupted by process restart"


@dataclass(frozen=True, slots=True)
class AgentSubmission:
    """保存可排队和持久化的执行主体任务。"""

    submission_id: str
    message: str
    kind: AgentSubmissionKind
    created_at_ms: int
    parent_turn_id: str = ""

    def __post_init__(self) -> None:
        """校验任务载荷中的稳定字段。"""
        submission_id = str(self.submission_id or "").strip()
        message = str(self.message or "").strip()
        parent_turn_id = str(self.parent_turn_id or "").strip()

        if not submission_id:
            raise ValueError("agent submission id is required")
        if not message:
            raise ValueError("agent submission message is required")
        if self.kind not in {"initial", "followup"}:
            raise ValueError("agent submission kind is invalid")
        if (
            isinstance(self.created_at_ms, bool)
            or not isinstance(self.created_at_ms, int)
            or self.created_at_ms <= 0
        ):
            raise ValueError("agent submission timestamp must be positive")

        object.__setattr__(self, "submission_id", submission_id)
        object.__setattr__(self, "message", message)
        object.__setattr__(self, "parent_turn_id", parent_turn_id)

    @classmethod
    def create(
        cls,
        message: str,
        *,
        kind: AgentSubmissionKind,
        parent_turn_id: str = "",
    ) -> "AgentSubmission":
        """创建带稳定标识和时间的任务载荷。"""
        if not isinstance(message, str):
            raise TypeError("agent submission message must be a string")
        return cls(
            submission_id=short_uid(12),
            message=message,
            kind=kind,
            created_at_ms=time.time_ns() // 1_000_000,
            parent_turn_id=parent_turn_id,
        )


__all__ = (
    "AgentResumeStatus",
    "AgentStatus",
    "AgentSubmission",
    "AgentSubmissionKind",
    "FINAL_AGENT_STATUSES",
    "RESTART_INTERRUPTION_ERROR",
)


if __name__ == '__main__':
    pass
