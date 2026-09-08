# -*- coding: utf-8 -*-

import pytest

from agent.adapters.protocol.items import CanonicalItemReducer
from protocol.schema.review import (
    ClientReviewWorkspace,
    ReviewCustomTarget,
    ReviewOutput,
)
from protocol.schema.stream_events import (
    ReviewCompletedEvent,
    ReviewStartedEvent,
)

CID = "cid_review_reducer"
SID = "sid_review_reducer_01"
TURN_ID = "turn_review_reducer_01"
ITEM_ID = "review_item_reducer_01"


def _started(event_seq: int = 1) -> ReviewStartedEvent:
    workspace = ClientReviewWorkspace.create()
    return ReviewStartedEvent(
        type="review.started",
        proto="mind.chat",
        cid=CID,
        sid=SID,
        turn_id=TURN_ID,
        event_seq=event_seq,
        item_id=ITEM_ID,
        item_kind="review",
        item_status="in_progress",
        review_item_id=ITEM_ID,
        status="in_progress",
        target=ReviewCustomTarget("Focus on lifecycle correctness."),
        workspace_revision=workspace.revision,
        prompt_version="mind-review/1",
    )


def _completed(event_seq: int = 2) -> ReviewCompletedEvent:
    return ReviewCompletedEvent(
        type="review.completed",
        proto="mind.chat",
        cid=CID,
        sid=SID,
        turn_id=TURN_ID,
        event_seq=event_seq,
        item_id=ITEM_ID,
        item_kind="review",
        item_status="completed",
        review_item_id=ITEM_ID,
        status="completed",
        output=ReviewOutput(
            findings=(),
            overall_correctness="correct",
            overall_explanation="No findings.",
            overall_confidence_score=0.9,
        ),
    )


def test_review_item_reducer_projects_terminal_output() -> None:
    reducer = CanonicalItemReducer(cid=CID, sid=SID, turn_id=TURN_ID)

    started = reducer.apply(_started())
    completed = reducer.apply(_completed())

    assert started is not None and started.item_status == "in_progress"
    assert completed is not None and completed.item_kind == "review"
    assert completed.item_status == "completed"
    assert completed.payload_value()["output"] == (
        _completed().output.payload()
    )
    assert reducer.canonical_items == (completed,)
    assert reducer.item_history == (completed,)


def test_review_item_reducer_rejects_duplicate_and_late_events() -> None:
    reducer = CanonicalItemReducer(cid=CID, sid=SID, turn_id=TURN_ID)
    reducer.apply(_started())

    with pytest.raises(ValueError, match="sequence must increase"):
        reducer.apply(_started())

    reducer.apply(_completed())
    with pytest.raises(ValueError, match="cannot transition"):
        reducer.apply(_started(event_seq=3))
