# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

from dataclasses import dataclass

from agent.application.hooks.models import StopHookDecision
from agent.application.turns.execution import (
    TurnExecution,
    create_continuation_execution,
)
from agent.application.turns.stream_outcome import StreamTurnOutcome
from agent.ports import ModelCapability
from observability import observe

MAX_STOP_CONTINUATIONS = 3


@dataclass(frozen=True, slots=True)
class TurnContinuation:
    """保存一次已获准 Stop Hook 续轮的执行和模型能力。"""

    execution: TurnExecution
    capability: ModelCapability


def turn_continuation_count(execution: TurnExecution) -> int:
    """读取模型执行的续跑次数。"""
    value = execution.metadata.get("continuation_count")
    try:
        count = int(value or 0)
    except (TypeError, ValueError):
        return 0
    return max(0, count)


def resolve_turn_continuation(
    execution: TurnExecution,
    outcome: StreamTurnOutcome,
    decision: StopHookDecision,
    capability: ModelCapability | None,
) -> TurnContinuation | None:
    """校验 Stop Hook 决议，并构造下一轮不可变执行。"""
    if not decision.should_continue:
        return None
    if not outcome.continuation_allowed or capability is None:
        observe(
            "hooks.stop.continuation_denied",
            level="WARNING",
            turn_id=execution.context.turn_id,
            outcome=outcome.status,
            can_continue=outcome.can_continue,
            stop_reason=outcome.terminal_meta.get("stop_reason"),
        )
        return None

    continuation_count = turn_continuation_count(execution)
    if continuation_count >= MAX_STOP_CONTINUATIONS:
        observe(
            "hooks.stop.limit_reached",
            level="WARNING",
            turn_id=execution.context.turn_id,
            continuation_count=continuation_count,
            hook_keys=list(decision.hook_keys),
        )
        return None
    return TurnContinuation(
        execution=create_continuation_execution(
            execution,
            decision.continuation_prompt,
            additional_context=decision.additional_context,
        ),
        capability=capability,
    )


if __name__ == '__main__':
    pass
