# -*- coding: utf-8 -*-
# Notes: ==== Mind™ ====

import typing

from agent.adapters.protocol.turn_source import ObservingTurnStreamSource
from agent.adapters.protocol.turn_stream import stream_turn
from agent.application.tools.execution import ToolExecutionAdapter
from agent.application.turns.execution import TurnExecution
from agent.application.turns.run_result import RunResult
from agent.ports import (
    EffectJournalFactory,
    McpSessionPort,
    ModelCapability,
    OutputSessionFactory,
    ProtocolCommandClient,
    TurnObservationCapability,
)


async def observe_stream_turn(
    session: McpSessionPort,
    pref_config: dict[str, typing.Any],
    tools: list[dict[str, typing.Any]],
    *_,
    turn_execution: TurnExecution,
    model_capability: ModelCapability | None = None,
    turn_observer: TurnObservationCapability | None = None,
    protocol_client: ProtocolCommandClient | None = None,
    effect_journal_factory: EffectJournalFactory | None = None,
    tool_execution: ToolExecutionAdapter | None = None,
    session_factory: OutputSessionFactory | None = None,
    **kwargs: typing.Any,
) -> RunResult:
    """只 attach 已提交 Turn，并复用共享工具、展示和终态生命周期。"""
    after_event_seq = kwargs.pop("observation_after_event_seq", None)
    replay_target_seq = kwargs.pop("observation_replay_target_seq", None)
    records_local_start = kwargs.pop("observation_records_local_start", True)
    if (
        after_event_seq is not None
        and (not isinstance(after_event_seq, int) or isinstance(after_event_seq, bool))
    ):
        raise TypeError("observation after_event_seq must be an integer")
    if (
        replay_target_seq is not None
        and (
            not isinstance(replay_target_seq, int)
            or isinstance(replay_target_seq, bool)
        )
    ):
        raise TypeError("observation replay_target_seq must be an integer")
    if not isinstance(records_local_start, bool):
        raise TypeError("observation records_local_start must be boolean")
    source = ObservingTurnStreamSource(
        turn_observer,
        model_capability,
        after_event_seq=after_event_seq,
        replay_target_seq=replay_target_seq,
        records_local_start=records_local_start,
    )
    return await stream_turn(
        session,
        pref_config,
        tools,
        turn_execution=turn_execution,
        turn_source=source,
        protocol_client=protocol_client,
        effect_journal_factory=effect_journal_factory,
        tool_execution=tool_execution,
        session_factory=session_factory,
        **kwargs,
    )


if __name__ == '__main__':
    pass
