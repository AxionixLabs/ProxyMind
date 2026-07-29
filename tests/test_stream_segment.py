# -*- coding: utf-8 -*-

from mind_app.stream_state.segment import SegmentTracker
from mind_nova.stream_events import (
    TextDeltaEvent,
    TextMetaEvent,
    ToolBuiltinDoneEvent,
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
