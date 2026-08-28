# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import enum
from dataclasses import dataclass


class RunStatus(enum.StrEnum):
    """列出阶段 1 主动 Turn 可到达的本地状态。"""

    CREATED = "created"
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    INCOMPLETE = "incomplete"
    INTERRUPTED = "interrupted"
    CANCELLED = "cancelled"
    RECONCILIATION_REQUIRED = "reconciliation_required"


_TRANSITIONS: dict[RunStatus, frozenset[RunStatus]] = {
    RunStatus.CREATED: frozenset({RunStatus.QUEUED}),
    RunStatus.QUEUED: frozenset({
        RunStatus.RUNNING,
        RunStatus.CANCELLED,
        RunStatus.FAILED,
    }),
    RunStatus.RUNNING: frozenset({
        RunStatus.COMPLETED,
        RunStatus.FAILED,
        RunStatus.INCOMPLETE,
        RunStatus.INTERRUPTED,
        RunStatus.CANCELLED,
        RunStatus.RECONCILIATION_REQUIRED,
    }),
}


@dataclass(slots=True)
class RunState:
    """持有一个 Run 的当前状态和本地事件序号。"""

    session_id: str
    run_id: str
    status: RunStatus = RunStatus.CREATED
    sequence: int = 0

    def transition(self, target: RunStatus) -> None:
        """执行一项受约束的不可逆状态转移。"""
        allowed = _TRANSITIONS.get(self.status, frozenset())
        if target not in allowed:
            raise ValueError(
                f"invalid run transition: {self.status.value} -> {target.value}"
            )
        self.status = target

    def next_sequence(self) -> int:
        """分配当前 Run 的下一个单调事件序号。"""
        self.sequence += 1
        return self.sequence


if __name__ == '__main__':
    pass
