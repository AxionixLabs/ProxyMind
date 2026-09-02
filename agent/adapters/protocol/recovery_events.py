# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing

from agent.adapters.protocol.activity_events import TurnActivityProjector
from agent.application.turns.presentation import (
    FailureProjectionMode,
    StreamTurnPresentation,
)
from agent.application.turns.stream_outcome import StreamTurnOutcome
from protocol.schema.stream_events import StreamGapEvent

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
        outcome.fail(
            "authoritative turn event sequence contains an internal gap",
            error_code="stream_gap_internal",
        )
        await activity.turn_terminal("failed")
        await presentation.emit_failure(
            "turn.stream_gap",
            mode=FailureProjectionMode.TERMINAL,
        )
        return "stop"

    await activity.recovery_changed(
        "replaying",
        event_seq=recovery_seq,
    )
    return "continue"


if __name__ == '__main__':
    pass
