# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import enum
from dataclasses import dataclass


class RunStatus(enum.StrEnum):
    """列出本地主动 Turn 的执行、等待、暂停和终止状态。"""

    CREATED = "created"
    QUEUED = "queued"
    RUNNING = "running"
    WAITING_APPROVAL = "waiting_approval"
    WAITING_EFFECT = "waiting_effect"
    PAUSED = "paused"
    COMPLETED = "completed"
    FAILED = "failed"
    INCOMPLETE = "incomplete"
    INTERRUPTED = "interrupted"
    CANCELLED = "cancelled"
    RECONCILIATION_REQUIRED = "reconciliation_required"


class RecoveryAction(enum.StrEnum):
    """列出重启后针对未终结 Run 允许采取的恢复动作。"""

    REDISPATCH = "redispatch"
    WAIT_APPROVAL = "wait_approval"
    RECONCILE = "reconcile"
    RESUME = "resume"


_TRANSITIONS: dict[RunStatus, frozenset[RunStatus]] = {
    RunStatus.CREATED: frozenset({RunStatus.QUEUED}),
    RunStatus.QUEUED: frozenset({
        RunStatus.RUNNING,
        RunStatus.CANCELLED,
        RunStatus.FAILED,
    }),
    RunStatus.RUNNING: frozenset({
        RunStatus.WAITING_APPROVAL,
        RunStatus.WAITING_EFFECT,
        RunStatus.PAUSED,
        RunStatus.COMPLETED,
        RunStatus.FAILED,
        RunStatus.INCOMPLETE,
        RunStatus.INTERRUPTED,
        RunStatus.CANCELLED,
        RunStatus.RECONCILIATION_REQUIRED,
    }),
    RunStatus.WAITING_APPROVAL: frozenset({
        RunStatus.RUNNING,
        RunStatus.CANCELLED,
        RunStatus.FAILED,
        RunStatus.PAUSED,
    }),
    RunStatus.WAITING_EFFECT: frozenset({
        RunStatus.RUNNING,
        RunStatus.CANCELLED,
        RunStatus.FAILED,
        RunStatus.RECONCILIATION_REQUIRED,
    }),
    RunStatus.PAUSED: frozenset({
        RunStatus.QUEUED,
        RunStatus.CANCELLED,
        RunStatus.FAILED,
    }),
}

RECOVERABLE_RUN_STATUSES = frozenset({
    RunStatus.QUEUED,
    RunStatus.RUNNING,
    RunStatus.WAITING_APPROVAL,
    RunStatus.WAITING_EFFECT,
    RunStatus.PAUSED,
    RunStatus.RECONCILIATION_REQUIRED,
})


def recovery_action(status: RunStatus) -> RecoveryAction:
    """把可恢复 Run 状态映射为不会盲目重复副作用的动作。"""
    actions = {
        RunStatus.QUEUED: RecoveryAction.REDISPATCH,
        RunStatus.RUNNING: RecoveryAction.RECONCILE,
        RunStatus.WAITING_APPROVAL: RecoveryAction.WAIT_APPROVAL,
        RunStatus.WAITING_EFFECT: RecoveryAction.RECONCILE,
        RunStatus.PAUSED: RecoveryAction.RESUME,
        RunStatus.RECONCILIATION_REQUIRED: RecoveryAction.RECONCILE,
    }
    try:
        return actions[status]
    except KeyError as error:
        raise ValueError(f"run status is not recoverable: {status.value}") from error


def validate_run_transition(current: RunStatus, target: RunStatus) -> None:
    """校验一项本地 Run 状态转移是否符合单写者规则。"""
    allowed = _TRANSITIONS.get(current, frozenset())
    if target not in allowed:
        raise ValueError(
            f"invalid run transition: {current.value} -> {target.value}"
        )


@dataclass(slots=True)
class RunState:
    """持有一个 Run 的当前状态和本地事件序号。"""

    session_id: str
    run_id: str
    status: RunStatus = RunStatus.CREATED
    sequence: int = 0

    def transition(self, target: RunStatus) -> None:
        """执行一项受约束的不可逆状态转移。"""
        validate_run_transition(self.status, target)
        self.status = target

    def next_sequence(self) -> int:
        """分配当前 Run 的下一个单调事件序号。"""
        self.sequence += 1
        return self.sequence


if __name__ == '__main__':
    pass
