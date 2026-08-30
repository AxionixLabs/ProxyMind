# -*- coding: utf-8 -*-

import typing

import pytest

from mind_app.runtime.hooks.models import StopHookDecision
from mind_app.runtime.turns.stream_finalize import StreamTurnFinalizer
from mind_app.runtime.turns.stream_outcome import StreamTurnOutcome
from mind_nova.stream_events import TurnDoneEvent


class _TurnStateStore:
    def __init__(self, name: str, operations: list[typing.Any]) -> None:
        self.name = name
        self.operations = operations

    def clear_turn(self, *, cid: str, sid: str, turn_id: str) -> None:
        self.operations.append((self.name, cid, sid, turn_id))


class _Transcript:
    def __init__(self, operations: list[typing.Any]) -> None:
        self.operations = operations
        self.entries: list[dict[str, typing.Any]] = []

    def append(self, event, *, actor=None, payload=None) -> None:
        self.operations.append(("transcript.append", event))
        self.entries.append({
            "event": event,
            "actor": actor,
            "payload": dict(payload or {}),
        })

    def close(self) -> None:
        self.operations.append("transcript.close")


class _Projection:
    def __init__(self, operations: list[typing.Any]) -> None:
        self.operations = operations

    def flush_pending(self, *, complete_only: bool = False) -> None:
        self.operations.append(("model.flush", complete_only))


class _IdleWait:
    def __init__(self, operations: list[typing.Any]) -> None:
        self.operations = operations

    async def cancel(self) -> None:
        self.operations.append("idle.cancel")


class _OutputControl:
    def __init__(self, operations: list[typing.Any]) -> None:
        self.operations = operations

    async def stop(self, *, blink: bool = True) -> None:
        self.operations.append(("output.stop", blink))


class _HookEvents:
    def __init__(
        self,
        operations: list[typing.Any],
        *,
        decision: StopHookDecision | None = None,
        error: Exception | None = None,
    ) -> None:
        self.operations = operations
        self.decision = decision or StopHookDecision.stop()
        self.error = error
        self.calls: list[dict[str, typing.Any]] = []

    async def stop(self, **kwargs) -> StopHookDecision:
        self.operations.append("hook.stop")
        self.calls.append(dict(kwargs))
        if self.error is not None:
            raise self.error
        return self.decision


def _completed_outcome() -> StreamTurnOutcome:
    """构造携带用量的已完成终态。"""
    outcome = StreamTurnOutcome()
    outcome.record_done_event(TurnDoneEvent(
        type="turn.done",
        turn_id="turn-test",
        usage={"output_tokens": 3},
    ))
    return outcome


def _finalizer(
    outcome: StreamTurnOutcome,
    operations: list[typing.Any],
    *,
    stream_end: bool = True,
) -> tuple[StreamTurnFinalizer, _Transcript]:
    """构造具有可观察资源端口的单轮收尾对象。"""
    transcript = _Transcript(operations)

    async def await_cleanup(awaitable):
        operations.append("cleanup.await")
        return await awaitable

    finalizer = StreamTurnFinalizer(
        cid="cid-test",
        sid="sid-test",
        turn_id="turn-test",
        outcome=outcome,
        turn_state_stores=(
            _TurnStateStore("permissions.clear", operations),
            _TurnStateStore("approvals.clear", operations),
        ),
        transcript=transcript,
        model_output=_Projection(operations),
        retry_state_close=lambda: operations.append("retry.close"),
        stream_end=(
            lambda reason: operations.append(("stream.end", reason))
            if stream_end
            else None
        ),
        idle_wait=_IdleWait(operations),
        output_control=_OutputControl(operations),
        await_cleanup=await_cleanup,
        continuation_count=2,
    )
    return finalizer, transcript


@pytest.mark.anyio
async def test_finalizer_closes_completed_turn_in_lifecycle_order() -> None:
    operations: list[typing.Any] = []
    outcome = _completed_outcome()
    finalizer, transcript = _finalizer(outcome, operations)
    continuation = StopHookDecision(
        should_continue=True,
        continuation_prompt="continue",
    )
    hooks = _HookEvents(operations, decision=continuation)

    decision = await finalizer.finalize(
        stream_end_reason="settled",
        hook_events=hooks,
        prompt_blocked=False,
        assistant_text="final answer",
    )

    assert decision == continuation
    assert operations == [
        ("permissions.clear", "cid-test", "sid-test", "turn-test"),
        ("approvals.clear", "cid-test", "sid-test", "turn-test"),
        "retry.close",
        ("stream.end", "settled"),
        ("model.flush", False),
        ("transcript.append", "turn.completed"),
        "hook.stop",
        "transcript.close",
        "idle.cancel",
        "cleanup.await",
        ("output.stop", True),
    ]
    assert transcript.entries == [{
        "event": "turn.completed",
        "actor": "system",
        "payload": {
            "status": "completed",
            "usage": {"output_tokens": 3},
        },
    }]
    assert hooks.calls == [{
        "outcome": "completed",
        "error": None,
        "usage": {"output_tokens": 3},
        "last_assistant_message": "final answer",
        "continuation_count": 2,
    }]


@pytest.mark.anyio
async def test_finalizer_discards_interrupted_stop_hook_decision() -> None:
    operations: list[typing.Any] = []
    outcome = StreamTurnOutcome()
    outcome.interrupt()
    finalizer, _transcript = _finalizer(
        outcome,
        operations,
        stream_end=False,
    )
    hooks = _HookEvents(
        operations,
        decision=StopHookDecision(
            should_continue=True,
            continuation_prompt="must not continue",
        ),
    )

    decision = await finalizer.finalize(
        stream_end_reason=None,
        hook_events=hooks,
        prompt_blocked=False,
        assistant_text="interrupted answer",
    )

    assert decision == StopHookDecision.stop()
    assert operations[-6:] == [
        "cleanup.await",
        "hook.stop",
        "transcript.close",
        "idle.cancel",
        "cleanup.await",
        ("output.stop", False),
    ]
    assert hooks.calls[0]["outcome"] == "interrupted"


@pytest.mark.anyio
async def test_finalizer_isolates_stop_hook_failure_from_resource_cleanup() -> None:
    operations: list[typing.Any] = []
    finalizer, _transcript = _finalizer(
        _completed_outcome(),
        operations,
        stream_end=False,
    )
    hooks = _HookEvents(
        operations,
        error=RuntimeError("stop hook failed"),
    )

    decision = await finalizer.finalize(
        stream_end_reason=None,
        hook_events=hooks,
        prompt_blocked=False,
        assistant_text="failed answer",
    )

    assert decision == StopHookDecision.stop()
    assert operations[-5:] == [
        "hook.stop",
        "transcript.close",
        "idle.cancel",
        "cleanup.await",
        ("output.stop", True),
    ]
