# -*- coding: utf-8 -*-

import typing

import pytest

from mind_app.presentation.output import SourcesOutput
from mind_app.presentation.models import (
    FailureView,
    RunCompletedView,
    RunIncompleteView,
    RunStartedView,
)
from mind_app.runtime.turns.stream_outcome import StreamTurnOutcome
from mind_app.runtime.turns.stream_presentation import (
    FailureProjectionMode,
    StreamTurnPresentation,
)
from agent.application import preset_permissions
from protocol.schema.stream_events import (
    TurnDoneEvent,
    TurnFailedEvent,
)


class _Sink:
    def __init__(self, operations: list[typing.Any] | None = None) -> None:
        self.items: list[typing.Any] = []
        self.operations = operations

    async def emit(self, item: typing.Any) -> None:
        if self.operations is not None:
            self.operations.append(("sink.emit", item))
        self.items.append(item)


class _Status:
    def __init__(self, operations: list[typing.Any] | None = None) -> None:
        self.end_calls: list[bool] = []
        self.operations = operations

    async def end_status(self, *, immediate: bool = False) -> None:
        if self.operations is not None:
            self.operations.append(("status.end", immediate))
        self.end_calls.append(immediate)


class _EventReport:
    def __init__(self, operations: list[typing.Any] | None = None) -> None:
        self.events: list[dict[str, typing.Any]] = []
        self.flushes = 0
        self.operations = operations

    def emit(self, event: dict[str, typing.Any]) -> None:
        if self.operations is not None:
            self.operations.append("report.emit")
        self.events.append(dict(event))

    async def flush(self) -> None:
        if self.operations is not None:
            self.operations.append("report.flush")
        self.flushes += 1


def _presentation(
    outcome: StreamTurnOutcome,
    *,
    operations: list[typing.Any] | None = None,
) -> tuple[
    StreamTurnPresentation,
    _Status,
    _Sink,
    _Sink,
    _EventReport,
]:
    """构造具有可观察输出端口的回合级展示对象。"""
    status = _Status(operations)
    content = _Sink(operations)
    views = _Sink(operations)
    report = _EventReport(operations)
    projection = StreamTurnPresentation(
        outcome=outcome,
        status_control=status,
        content=content,
        presentation=views,
        event_report=report,
    )
    return projection, status, content, views, report


@pytest.mark.anyio
async def test_presentation_emits_frozen_run_start_view() -> None:
    projection, _status, _content, views, _report = _presentation(
        StreamTurnOutcome()
    )
    permissions = preset_permissions("auto")

    await projection.emit_started(
        metadata={"cid": "cid-test", "sid": "sid-test"},
        message="hello",
        pref_config={
            "primary": {
                "name": "provider-test",
                "model": "model-test",
                "reasoning_effort": "high",
            },
        },
        workdir="/workspace",
        permissions=permissions,
        turn_id="turn-test",
        hook_warnings=("warning",),
    )

    assert views.items == [RunStartedView(
        thread_id="cid-test",
        turn_id="turn-test",
        session_id="sid-test",
        message="hello",
        model="model-test",
        provider="provider-test",
        approval=permissions.approval_policy,
        workdir="/workspace",
        sandbox=permissions.sandbox_mode,
        reasoning_effort="high",
        reasoning_summaries="none",
        hook_warnings=("warning",),
    )]


@pytest.mark.anyio
async def test_presentation_reports_local_failure_before_emitting_view() -> None:
    operations: list[typing.Any] = []
    outcome = StreamTurnOutcome()
    outcome.fail("local failure")
    projection, status, _content, views, report = _presentation(
        outcome,
        operations=operations,
    )

    await projection.emit_failure("turn.failed")

    assert [operation if isinstance(operation, str) else operation[0]
            for operation in operations] == [
        "report.emit",
        "report.flush",
        "status.end",
        "sink.emit",
    ]
    assert status.end_calls == [True]
    assert report.flushes == 1
    assert report.events[0]["type"] == "turn.failed"
    assert report.events[0]["error"] == "local failure"
    assert isinstance(report.events[0]["ts"], float)
    assert views.items == [FailureView(
        phase="turn.failed",
        error="local failure",
    )]


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("mode", "expected_usage", "expected_stop_reason"),
    (
        (
            FailureProjectionMode.TERMINAL,
            {"output_tokens": 4},
            "pause_turn",
        ),
        (FailureProjectionMode.PROJECTION_ONLY, {}, None),
    ),
)
async def test_presentation_projects_protocol_failure_without_reporting(
    mode: FailureProjectionMode,
    expected_usage: dict[str, typing.Any],
    expected_stop_reason: str | None,
) -> None:
    outcome = StreamTurnOutcome()
    outcome.record_failed_event(TurnFailedEvent(
        type="turn.failed",
        turn_id="turn-test",
        error="protocol failure",
        usage={"output_tokens": 4},
        stop_reason="pause_turn",
    ))
    projection, status, _content, views, report = _presentation(outcome)

    await projection.emit_failure("turn.failed", mode=mode)

    assert report.events == []
    assert report.flushes == 0
    assert status.end_calls == [True]
    assert views.items == [FailureView(
        phase="turn.failed",
        error="protocol failure",
        usage=expected_usage,
        stop_reason=expected_stop_reason,
    )]


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("event", "expected_view"),
    (
        (
            TurnDoneEvent(
                type="turn.done",
                turn_id="turn-test",
                usage={"output_tokens": 3},
                response_id="response-test",
            ),
            RunCompletedView(
                usage={"output_tokens": 3},
                response_id="response-test",
            ),
        ),
        (
            TurnDoneEvent(
                type="turn.done",
                turn_id="turn-test",
                status="incomplete",
                reason="max_output_tokens",
                can_continue=True,
                usage={"output_tokens": 5},
                stop_reason="max_tokens",
            ),
            RunIncompleteView(
                usage={"output_tokens": 5},
                reason="max_output_tokens",
                can_continue=True,
                stop_reason="max_tokens",
            ),
        ),
    ),
)
async def test_presentation_projects_sources_and_normal_terminal_view(
    event: TurnDoneEvent,
    expected_view: RunCompletedView | RunIncompleteView,
) -> None:
    outcome = StreamTurnOutcome()
    outcome.record_done_event(event)
    projection, status, content, views, report = _presentation(outcome)

    await projection.emit_result(({"url": "https://example.test"},))

    assert status.end_calls == [False]
    assert content.items == [SourcesOutput(({
        "url": "https://example.test",
    },))]
    assert views.items == [expected_view]
    assert report.events == []
