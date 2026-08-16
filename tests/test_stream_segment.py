# -*- coding: utf-8 -*-

from mind_app.stream_state.segment import SegmentTracker
from mind_nova.stream_events import (
    PresentationSupersededEvent,
    TextDeltaEvent,
    TextMetaEvent,
    ToolBuiltinDoneEvent,
    TurnRetryingEvent,
)


def test_segment_tracker_applies_metadata_received_before_text() -> None:
    tracker = SegmentTracker()
    tracker.on_text_meta(TextMetaEvent(
        type="text.meta",
        segment_id="remote-1",
        annotations=({"start": 0},),
        citations=({"source": 0},),
        sources=({"url": "https://example.com"},),
        source_count=1,
    ))

    tracker.on_text_delta(TextDeltaEvent(
        type="text.delta",
        segment_id="remote-1",
        text="answer",
    ))

    segment = tracker.segments_by_key[tracker.segment_order[0]]
    assert segment["annotations"] == [{"start": 0}]
    assert segment["citations"] == [{"source": 0}]
    assert segment["sources"] == [{"url": "https://example.com"}]
    assert segment["source_count"] == 1
    assert list(tracker.iter_sources()) == [{"url": "https://example.com"}]


def test_segment_tracker_binds_builtin_sources_to_next_text_segment() -> None:
    tracker = SegmentTracker()
    tracker.on_builtin_done(ToolBuiltinDoneEvent(
        type="tool.builtin.done",
        sources=({"url": "https://example.com/tool"},),
    ))

    tracker.on_text_delta(TextDeltaEvent(
        type="text.delta",
        segment_id="remote-2",
        text="result",
    ))

    segment = tracker.segments_by_key[tracker.segment_order[0]]
    assert segment["sources"] == [{"url": "https://example.com/tool"}]
    assert segment["source_count"] == 1
    assert tracker.pending_segment_sources is None


def test_superseded_epoch_is_excluded_from_canonical_text_and_sources() -> None:
    tracker = SegmentTracker()
    tracker.on_text_delta(TextDeltaEvent(
        type="text.delta",
        text="old",
        segment_id="old-segment",
        presentation_epoch=1,
    ))
    tracker.on_text_meta(TextMetaEvent(
        type="text.meta",
        segment_id="old-segment",
        sources=({"url": "https://old.example"},),
        presentation_epoch=1,
    ))
    tracker.on_presentation_superseded(PresentationSupersededEvent(
        type="presentation.superseded",
        presentation_epoch=2,
        superseded_epoch=1,
    ))
    tracker.on_text_delta(TextDeltaEvent(
        type="text.delta",
        text="new",
        segment_id="new-segment",
        presentation_epoch=2,
    ))

    assert tracker.assistant_text() == "new"
    assert list(tracker.iter_sources()) == []


def test_retry_supersedes_already_committed_output_in_same_epoch() -> None:
    tracker = SegmentTracker()
    tracker.on_text_delta(TextDeltaEvent(
        type="text.delta",
        text="old",
        segment_id="attempt-1",
        presentation_epoch=1,
        round=1,
    ))
    assert tracker.commit_assistant_output() == "old"

    replaced = tracker.on_turn_retrying(TurnRetryingEvent(
        type="turn.retrying",
        presentation_epoch=1,
        round=1,
        attempt=2,
        max_attempts=3,
        retry_in_ms=100,
        replace_current_response=True,
    ))
    tracker.on_text_delta(TextDeltaEvent(
        type="text.delta",
        text="new",
        segment_id="attempt-2",
        presentation_epoch=1,
        round=1,
    ))

    assert replaced is True
    assert tracker.assistant_text() == "new"


def test_retry_only_supersedes_current_model_round() -> None:
    """验证后一模型 round 重试时保留前序 round 的成功正文。"""
    tracker = SegmentTracker()
    tracker.on_text_delta(TextDeltaEvent(
        type="text.delta",
        text="round one",
        segment_id="round-1-attempt-1",
        presentation_epoch=1,
        round=1,
    ))
    tracker.commit_assistant_output()
    tracker.on_text_delta(TextDeltaEvent(
        type="text.delta",
        text="round two partial",
        segment_id="round-2-attempt-1",
        presentation_epoch=1,
        round=2,
    ))

    replaced = tracker.on_turn_retrying(TurnRetryingEvent(
        type="turn.retrying",
        presentation_epoch=1,
        round=2,
        attempt=2,
        max_attempts=3,
        retry_in_ms=100,
        replace_current_response=True,
    ))
    tracker.on_text_delta(TextDeltaEvent(
        type="text.delta",
        text="round two final",
        segment_id="round-2-attempt-2",
        presentation_epoch=1,
        round=2,
    ))

    assert replaced is True
    assert tracker.assistant_text() == "round one\nround two final"
    assert [
        segment["superseded"]
        for segment in tracker.segments_by_key.values()
    ] == [False, True, False]
