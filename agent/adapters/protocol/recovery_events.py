# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import asyncio
import typing

from agent.adapters.protocol.activity_events import TurnActivityProjector
from agent.adapters.protocol.turn_interrupts import cancel_reconciliation_turn
from agent.application.turns.presentation import (
    FailureProjectionMode,
    StreamTurnPresentation,
)
from agent.application.turns.stream_outcome import StreamTurnOutcome
from agent.ports import ProtocolCommandClient
from observability import observe
from protocol.schema.stream_events import (
    StreamGapEvent,
    TurnReconciliationRequiredEvent,
)

StreamGapDecision: typing.TypeAlias = typing.Literal["continue", "stop"]


async def handle_stream_gap(
    event: StreamGapEvent,
    *,
    activity: TurnActivityProjector,
    outcome: StreamTurnOutcome,
    presentation: StreamTurnPresentation,
) -> StreamGapDecision:
    """把非持久传输缺口收窄为恢复投影或确定失败。"""
    recovery_seq = (
        event.next_seq
        if event.next_seq is not None
        else event.expected_event_seq
        if event.expected_event_seq is not None
        else activity.event_seq
    )
    if event.gap_kind == "internal":
        await activity.recovery_changed("gap", event_seq=recovery_seq)
        outcome.mark_delivery_incomplete(
            "authoritative turn event sequence contains an internal gap",
            error_code="stream_gap_internal",
        )
        await presentation.emit_failure(
            "turn.stream_gap",
            mode=FailureProjectionMode.PROJECTION_ONLY,
        )
        return "continue"

    await activity.recovery_changed(
        "replaying",
        event_seq=recovery_seq,
    )
    return "continue"


async def handle_turn_reconciliation(
    event: TurnReconciliationRequiredEvent,
    *,
    reconcile_known_effect: typing.Callable[[str], typing.Awaitable[bool]],
    client: ProtocolCommandClient,
    cid: str,
    sid: str,
    turn_id: str,
    activity: TurnActivityProjector,
    outcome: StreamTurnOutcome,
    presentation: StreamTurnPresentation,
) -> bool:
    """核对已知执行效果，失败时释放轮次并投影待核对状态。"""
    try:
        recovered = await reconcile_known_effect(event.effect_id)
    except asyncio.CancelledError:
        raise
    except Exception as recovery_error:
        recovered = False
        observe(
            "stream.reconciliation_auto_recovery_failed",
            level="WARNING",
            turn_id=turn_id,
            effect_id=event.effect_id,
            error=f"{type(recovery_error).__name__}: {recovery_error}",
        )
    if recovered:
        observe(
            "stream.reconciliation_auto_recovered",
            turn_id=turn_id,
            effect_id=event.effect_id,
        )
        await activity.request_model_wait("tool_result")
        return True

    cancelled = await cancel_reconciliation_turn(
        client,
        cid=cid,
        sid=sid,
        turn_id=turn_id,
        effect_id=event.effect_id,
    )
    reconciliation_error = event.error or "effect outcome requires reconciliation"
    if not cancelled:
        reconciliation_error = (
            f"{reconciliation_error}; failed to release the suspended turn"
        )
    outcome.require_reconciliation(reconciliation_error)
    await activity.turn_terminal("reconciliation_required")
    observe(
        "stream.reconciliation_required",
        level="ERROR",
        turn_id=turn_id,
        effect_id=event.effect_id,
        error=outcome.error,
        turn_released=cancelled,
    )
    await presentation.emit_failure(
        "turn.reconciliation_required",
        mode=FailureProjectionMode.PROJECTION_ONLY,
    )
    return False


if __name__ == '__main__':
    pass
