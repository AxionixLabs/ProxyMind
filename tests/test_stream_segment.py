# -*- coding: utf-8 -*-

import pytest

from mind_app.stream_state.segment import SegmentTracker
from mind_nova.stream_events import (
    PresentationSupersededEvent,
    TextDeltaEvent,
    TextDoneEvent,
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

    old_key = tracker.segments_by_remote_id["old-segment"]
    new_key = tracker.segments_by_remote_id["new-segment"]
    assert tracker.segments_by_key[old_key]["superseded"] is True
    assert tracker.segments_by_key[new_key]["superseded"] is False
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
    assert tracker.drain_assistant_outputs() == [((1, 1, 1), "attempt-1", "old")]

    replaced = tracker.on_turn_retrying(TurnRetryingEvent(
        type="turn.retrying",
        presentation_epoch=1,
        round=1,
        attempt=2,
        max_attempts=3,
        retry_in_ms=100,
    ))
    tracker.on_text_delta(TextDeltaEvent(
        type="text.delta",
        text="new",
        segment_id="attempt-2",
        presentation_epoch=1,
        round=1,
    ))

    assert replaced is True
    assert tracker.drain_assistant_outputs() == [
        ((1, 1, 2), "attempt-2", "new"),
    ]


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
    assert tracker.drain_assistant_outputs() == [
        ((1, 1, 1), "round-1-attempt-1", "round one")
    ]
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
    ))
    tracker.on_text_delta(TextDeltaEvent(
        type="text.delta",
        text="round two final",
        segment_id="round-2-attempt-2",
        presentation_epoch=1,
        round=2,
    ))

    assert replaced is True
    assert [
        segment["superseded"]
        for segment in tracker.segments_by_key.values()
    ] == [False, True, False]
    assert tracker.drain_assistant_outputs() == [
        ((1, 2, 2), "round-2-attempt-2", "round two final"),
    ]


def test_pending_outputs_are_drained_per_response_identity() -> None:
    tracker = SegmentTracker()
    tracker.on_text_delta(TextDeltaEvent(
        type="text.delta",
        text="first",
        segment_id="item-one",
        round=1,
    ))
    tracker.on_text_delta(TextDeltaEvent(
        type="text.delta",
        text="second",
        segment_id="item-two",
        round=2,
    ))

    assert tracker.drain_assistant_outputs() == [
        ((1, 1, 1), "item-one", "first"),
        ((1, 2, 1), "item-two", "second"),
    ]
    assert tracker.drain_assistant_outputs() == []


def test_pending_outputs_are_not_mixed_when_drained() -> None:
    tracker = SegmentTracker()
    tracker.on_text_delta(TextDeltaEvent(
        type="text.delta",
        text="first",
        segment_id="item-one",
        round=1,
    ))
    tracker.on_text_delta(TextDeltaEvent(
        type="text.delta",
        text="second",
        segment_id="item-two",
        round=2,
    ))

    assert tracker.drain_assistant_outputs() == [
        ((1, 1, 1), "item-one", "first"),
        ((1, 2, 1), "item-two", "second"),
    ]
    assert tracker.pending_output_item_order == []


def test_item_id_reuse_across_response_identity_is_rejected() -> None:
    tracker = SegmentTracker()
    tracker.on_text_delta(TextDeltaEvent(
        type="text.delta",
        text="first",
        segment_id="reused-item",
        round=1,
    ))
    tracker.on_text_done(TextDoneEvent(
        type="text.done",
        segment_id="reused-item",
        round=1,
    ))

    with pytest.raises(ValueError, match="item_id cannot cross response identity"):
        tracker.on_text_delta(TextDeltaEvent(
            type="text.delta",
            text="second",
            segment_id="reused-item",
            round=2,
        ))


def test_text_done_final_text_replaces_incomplete_deltas() -> None:
    tracker = SegmentTracker()
    tracker.on_text_delta(TextDeltaEvent(
        type="text.delta",
        text="partial",
        segment_id="item-1",
    ))

    completed = tracker.on_text_done(TextDoneEvent(
        type="text.done",
        segment_id="item-1",
        final_text="complete answer",
    ))

    assert completed == "complete answer"
    assert tracker.drain_assistant_outputs() == [
        ((1, 1, 1), "item-1", "complete answer")
    ]


def test_pending_outputs_are_not_mixed_for_items_in_same_response() -> None:
    tracker = SegmentTracker()
    tracker.on_text_delta(TextDeltaEvent(
        type="text.delta",
        text="first",
        segment_id="item-one",
    ))
    tracker.on_text_done(TextDoneEvent(
        type="text.done",
        segment_id="item-one",
    ))
    tracker.on_text_delta(TextDeltaEvent(
        type="text.delta",
        text="second",
        segment_id="item-two",
    ))
    tracker.on_text_done(TextDoneEvent(
        type="text.done",
        segment_id="item-two",
    ))

    assert tracker.drain_assistant_outputs() == [
        ((1, 1, 1), "item-one", "first"),
        ((1, 1, 1), "item-two", "second"),
    ]


def test_incomplete_items_wait_for_final_drain() -> None:
    tracker = SegmentTracker()
    tracker.on_text_delta(TextDeltaEvent(
        type="text.delta",
        text="partial",
        segment_id="item-1",
    ))

    assert tracker.drain_assistant_outputs(complete_only=True) == []
    assert tracker.drain_assistant_outputs() == [
        ((1, 1, 1), "item-1", "partial")
    ]


def test_late_delta_for_superseded_item_is_ignored() -> None:
    tracker = SegmentTracker()
    tracker.on_text_delta(TextDeltaEvent(
        type="text.delta",
        text="old",
        segment_id="item-old",
    ))
    tracker.on_turn_retrying(TurnRetryingEvent(
        type="turn.retrying",
        round=1,
        attempt=2,
        max_attempts=3,
        retry_in_ms=0,
        supersedes_item_id="item-old",
    ))

    assert tracker.on_text_delta(TextDeltaEvent(
        type="text.delta",
        text="late old",
        segment_id="item-old",
        round=1,
    )) is False
    assert tracker.on_text_done(TextDoneEvent(
        type="text.done",
        segment_id="item-old",
        round=1,
    )) == ""
    assert tracker.should_ignore_item("item-old") is True


def test_explicit_retry_supersedes_only_named_item() -> None:
    tracker = SegmentTracker()
    tracker.on_text_delta(TextDeltaEvent(
        type="text.delta",
        text="old one",
        segment_id="item-old-one",
        round=1,
    ))
    tracker.on_text_delta(TextDeltaEvent(
        type="text.delta",
        text="old two",
        segment_id="item-old-two",
        round=1,
    ))

    tracker.on_turn_retrying(TurnRetryingEvent(
        type="turn.retrying",
        round=1,
        attempt=2,
        max_attempts=3,
        retry_in_ms=0,
        supersedes_item_id="item-old-one",
    ))

    assert tracker.segments_by_key["segment-1"]["superseded"] is True
    assert tracker.segments_by_key["segment-2"]["superseded"] is False


def test_explicit_retry_supersedes_named_item_across_round_metadata() -> None:
    tracker = SegmentTracker()
    tracker.on_text_delta(TextDeltaEvent(
        type="text.delta",
        text="old",
        segment_id="item-old",
        round=1,
    ))
    tracker.on_text_delta(TextDeltaEvent(
        type="text.delta",
        text="keep",
        segment_id="item-keep",
        round=2,
    ))

    tracker.on_turn_retrying(TurnRetryingEvent(
        type="turn.retrying",
        round=2,
        attempt=2,
        max_attempts=3,
        retry_in_ms=0,
        supersedes_item_id="item-old",
    ))

    assert tracker.segments_by_key["segment-1"]["superseded"] is True
    assert tracker.segments_by_key["segment-2"]["superseded"] is False
