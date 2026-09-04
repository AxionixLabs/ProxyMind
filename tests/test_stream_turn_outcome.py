# -*- coding: utf-8 -*-

from agent.application.turns.run_result import RunResult
from agent.application.turns.stream_outcome import StreamTurnOutcome
from protocol.schema.stream_events import TurnCompletedEvent


def test_outcome_builds_completed_result_from_single_terminal() -> None:
    outcome = StreamTurnOutcome()
    outcome.record_completed_event(TurnCompletedEvent(
        type="turn.completed",
        status="completed",
        last_event_seq=7,
        completed_at=1.0,
        usage={"output_tokens": 7},
        response_id="msg_1",
        route="messages",
    ))

    assert outcome.build_result("answer") == RunResult(
        status="completed",
        assistant_text="answer",
        usage={"output_tokens": 7},
        response_id="msg_1",
        route="messages",
    )
    assert outcome.has_terminal_status is True
    assert outcome.continuation_allowed is True
    assert outcome.observation_outcome == "complete"


def test_outcome_prioritizes_reconciliation_over_failed_terminal() -> None:
    outcome = StreamTurnOutcome()
    outcome.record_completed_event(TurnCompletedEvent(
        type="turn.completed",
        status="failed",
        last_event_seq=3,
        completed_at=1.0,
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
    outcome.record_completed_event(TurnCompletedEvent(
        type="turn.completed",
        status="failed",
        last_event_seq=4,
        completed_at=1.0,
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


def test_outcome_preserves_delivery_gap_after_remote_completion() -> None:
    outcome = StreamTurnOutcome()
    outcome.mark_delivery_incomplete(
        "authoritative events are missing",
        error_code="stream_gap_internal",
    )

    outcome.record_completed_event(TurnCompletedEvent(
        type="turn.completed",
        status="completed",
        last_event_seq=9,
        completed_at=1.0,
        usage={"output_tokens": 2},
    ))

    assert outcome.build_result("partial") == RunResult(
        status="incomplete",
        assistant_text="partial",
        usage={"output_tokens": 2},
        error="authoritative events are missing",
        error_code="stream_gap_internal",
    )
    assert outcome.has_terminal_status is True
    assert outcome.continuation_allowed is False


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
