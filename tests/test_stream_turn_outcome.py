# -*- coding: utf-8 -*-

from agent.application.turns.run_result import RunResult
from agent.application.turns.stream_outcome import StreamTurnOutcome
from protocol.schema.stream_events import (
    TurnDoneEvent,
    TurnFailedEvent,
)


def test_outcome_builds_incomplete_result_and_allows_continuation() -> None:
    outcome = StreamTurnOutcome()
    outcome.record_done_event(TurnDoneEvent(
        type="turn.done",
        status="incomplete",
        reason="max_output_tokens",
        can_continue=True,
        usage={"output_tokens": 7},
        response_id="msg_1",
        route="messages",
        stop_reason="max_tokens",
    ))

    assert outcome.build_result("partial") == RunResult(
        status="incomplete",
        assistant_text="partial",
        usage={"output_tokens": 7},
        error="max_output_tokens",
        response_id="msg_1",
        route="messages",
        stop_reason="max_tokens",
        reason="max_output_tokens",
        can_continue=True,
    )
    assert outcome.has_terminal_status is True
    assert outcome.continuation_allowed is True
    assert outcome.observation_outcome == "incomplete"


def test_outcome_prioritizes_reconciliation_over_failed_terminal() -> None:
    outcome = StreamTurnOutcome()
    outcome.record_failed_event(TurnFailedEvent(
        type="turn.failed",
        error="provider failed",
        usage={"output_tokens": 2},
        stop_reason="provider_error",
    ))
    outcome.require_reconciliation("effect result is unknown")

    assert outcome.status == "reconciliation_required"
    assert outcome.error == "effect result is unknown"
    assert outcome.is_failed is True
    assert outcome.is_reconciliation_required is True
    assert outcome.observation_outcome == "reconciliation_required"
    assert outcome.continuation_allowed is False
    assert outcome.build_result("") == RunResult(
        status="reconciliation_required",
        usage={"output_tokens": 2},
        error="effect result is unknown",
        stop_reason="provider_error",
    )


def test_outcome_preserves_server_failure_metadata() -> None:
    outcome = StreamTurnOutcome()
    outcome.record_failed_event(TurnFailedEvent(
        type="turn.failed",
        error="content rejected",
        error_type="provider_error",
        error_source="provider",
        status_code=422,
        retryable=False,
    ))

    assert outcome.build_result("") == RunResult(
        status="failed",
        error="content rejected",
        error_code="provider_error",
        error_details={
            "source": "provider",
            "status_code": 422,
            "retryable": False,
        },
    )


def test_outcome_marks_stream_without_terminal_as_incomplete() -> None:
    outcome = StreamTurnOutcome()

    outcome.settle_stream()

    assert outcome.has_terminal_status is False
    assert outcome.status == "incomplete"
    assert outcome.error == "stream ended before turn completion"
    assert outcome.continuation_allowed is False
    assert outcome.build_result("") == RunResult(
        status="incomplete",
        error="stream ended before turn completion",
    )


def test_outcome_preserves_named_capability_error() -> None:
    outcome = StreamTurnOutcome()
    outcome.fail(
        "service unavailable",
        error_code="model_transport_http_error",
        error_details={"status_code": 503},
    )

    assert outcome.build_result("") == RunResult(
        status="failed",
        error="service unavailable",
        error_code="model_transport_http_error",
        error_details={"status_code": 503},
    )
