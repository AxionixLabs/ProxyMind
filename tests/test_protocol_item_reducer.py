# -*- coding: utf-8 -*-

import pytest

from agent.adapters.protocol.items import CanonicalItemReducer
from protocol.schema.stream_events import (
    ToolApprovalRequiredEvent,
    parse_stream_event,
)
from protocol.schema.tool_approval import (
    ToolApprovalSnapshot,
    ToolApprovalSnapshotItem,
)


def _payload(
    event_type: str,
    *,
    event_seq: int,
    presentation_epoch: int = 1,
    round_no: int = 1,
    **values,
) -> dict:
    payload = {
        "type": event_type,
        "proto": "mind.chat",
        "cid": "cid_test",
        "sid": "sid_test",
        "turn_id": "turn_test",
        "event_seq": event_seq,
        "presentation_epoch": presentation_epoch,
        "round": round_no,
        **values,
    }
    if event_type.startswith("text."):
        segment_id = str(values.get("segment_id") or "segment_test")
        payload.setdefault("segment_id", segment_id)
        payload.setdefault("item_id", segment_id)
        payload.setdefault("item_kind", "text")
        payload.setdefault(
            "item_status",
            "in_progress" if event_type == "text.delta" else "completed",
        )
    return payload


def _reducer() -> CanonicalItemReducer:
    return CanonicalItemReducer(
        cid="cid_test",
        sid="sid_test",
        turn_id="turn_test",
    )


def _approval_snapshot(
    status: str,
    *,
    last_event_seq: int,
) -> ToolApprovalSnapshot:
    ack = (
        {
            "decision": "accept",
            "tool_status": "approved",
        }
        if status == "resolved"
        else None
    )
    return ToolApprovalSnapshot(
        cid="cid_test",
        sid="sid_test",
        turn_id="turn_test",
        turn_status="waiting_approval",
        terminal=None,
        last_event_seq=last_event_seq,
        approvals=(ToolApprovalSnapshotItem(
            approval_id="approval_test",
            turn_id="turn_test",
            call_id="call_test",
            kind="command",
            approval={
                "type": "tool.approval_required",
                "item_id": "approval_test",
                "item_kind": "approval",
                "item_status": "waiting_approval",
                "event_seq": 2,
                "presentation_epoch": 1,
                "approval_id": "approval_test",
                "call_id": "call_test",
                "kind": "command",
                "available_decisions": ["accept", "decline", "cancel"],
            },
            status=status,
            ack=ack,
        ),),
    )


def _approval_event(*, event_seq: int) -> ToolApprovalRequiredEvent:
    return ToolApprovalRequiredEvent(
        type="tool.approval_required",
        proto="mind.chat",
        cid="cid_test",
        sid="sid_test",
        turn_id="turn_test",
        event_seq=event_seq,
        presentation_epoch=1,
        item_id="approval_test",
        item_kind="approval",
        item_status="waiting_approval",
        approval_id="approval_test",
        call_id="call_test",
        kind="command",
        status="pending",
        ack=None,
        available_decisions=("accept", "decline", "cancel"),
    )


def test_reducer_merges_early_metadata_and_final_text() -> None:
    reducer = _reducer()

    assert reducer.apply(parse_stream_event(_payload(
        "text.meta",
        event_seq=1,
        sources=[{"url": "https://example.com"}],
        source_count=1,
        phase="commentary",
    ))) is None
    reducer.apply(parse_stream_event(_payload(
        "text.delta",
        event_seq=2,
        text="partial",
        phase="commentary",
    )))
    completed = reducer.apply(parse_stream_event(_payload(
        "text.done",
        event_seq=3,
        final_text="complete",
        phase="commentary",
    )))

    assert completed is not None
    assert completed.item_status == "completed"
    assert completed.first_event_seq == 1
    assert completed.last_event_seq == 3
    assert completed.phase == "commentary"
    assert completed.payload_value() == {
        "sources": [{"url": "https://example.com"}],
        "source_count": 1,
        "text": "complete",
    }
    assert reducer.assistant_text == "complete"


def test_reducer_rejects_assistant_text_phase_changes() -> None:
    """验证 phase 在早到 metadata 和后续正文之间必须保持不变。"""
    reducer = _reducer()
    reducer.apply(parse_stream_event(_payload(
        "text.meta",
        event_seq=1,
        phase="commentary",
    )))

    with pytest.raises(ValueError, match="phase cannot change"):
        reducer.apply(parse_stream_event(_payload(
            "text.delta",
            event_seq=2,
            text="answer",
            phase="final_answer",
        )))
    recovered = reducer.apply(parse_stream_event(_payload(
        "text.delta",
        event_seq=2,
        text="answer",
        phase="commentary",
    )))
    assert recovered is not None
    assert recovered.phase == "commentary"

    reducer = _reducer()
    reducer.apply(parse_stream_event(_payload(
        "text.delta",
        event_seq=1,
        text="answer",
        phase="final_answer",
    )))
    with pytest.raises(ValueError, match="phase cannot change"):
        reducer.apply(parse_stream_event(_payload(
            "text.done",
            event_seq=2,
            final_text="answer",
            phase=None,
        )))


def test_reducer_rejects_item_status_regression() -> None:
    reducer = _reducer()
    reducer.apply(parse_stream_event(_payload(
        "text.done",
        event_seq=1,
        final_text="complete",
    )))

    with pytest.raises(
        ValueError,
        match="status cannot transition from completed to in_progress",
    ):
        reducer.apply(parse_stream_event(_payload(
            "text.delta",
            event_seq=2,
            text="late",
        )))


def test_retry_supersedes_only_current_round_attempt() -> None:
    reducer = _reducer()
    reducer.apply(parse_stream_event(_payload(
        "text.delta",
        event_seq=1,
        round_no=1,
        segment_id="round_one",
        text="round one",
    )))
    reducer.apply(parse_stream_event(_payload(
        "text.delta",
        event_seq=2,
        round_no=2,
        segment_id="round_two_old",
        text="old",
    )))
    reducer.apply(parse_stream_event(_payload(
        "turn.retrying",
        event_seq=3,
        round_no=2,
        attempt=2,
        max_attempts=3,
        retry_in_ms=0,
        reason="provider_error",
        error_type="provider_error",
        error={
            "type": "provider_error",
            "source": "provider",
            "retryable": True,
        },
    )))
    reducer.apply(parse_stream_event(_payload(
        "text.delta",
        event_seq=4,
        round_no=2,
        segment_id="round_two_new",
        text="new",
    )))

    assert reducer.assistant_text == "round one\nnew"
    assert [item.item_id for item in reducer.canonical_items] == [
        "round_one",
        "round_two_new",
    ]
    history = {item.item_id: item for item in reducer.item_history}
    assert history["round_two_old"].superseded is True
    assert history["round_two_old"].superseded_by_attempt == 2
    assert history["round_one"].superseded is False


def test_retry_ignores_late_events_for_superseded_item() -> None:
    reducer = _reducer()
    reducer.apply(parse_stream_event(_payload(
        "text.delta",
        event_seq=1,
        segment_id="old_item",
        text="old",
    )))
    reducer.apply(parse_stream_event(_payload(
        "turn.retrying",
        event_seq=2,
        attempt=2,
        max_attempts=3,
        retry_in_ms=0,
        reason="provider_error",
        error_type="provider_error",
        supersedes_item_id="old_item",
        error={
            "type": "provider_error",
            "source": "provider",
            "retryable": True,
        },
    )))

    assert reducer.apply(parse_stream_event(_payload(
        "text.delta",
        event_seq=3,
        segment_id="old_item",
        text="late old",
    ))) is None
    reducer.apply(parse_stream_event(_payload(
        "text.delta",
        event_seq=4,
        segment_id="new_item",
        text="new",
    )))

    assert reducer.assistant_text == "new"
    assert [item.item_id for item in reducer.canonical_items] == ["new_item"]


def test_presentation_supersede_keeps_audit_revision() -> None:
    reducer = _reducer()
    reducer.apply(parse_stream_event(_payload(
        "text.delta",
        event_seq=1,
        presentation_epoch=1,
        segment_id="old_item",
        text="old",
    )))
    reducer.apply(parse_stream_event(_payload(
        "presentation.superseded",
        event_seq=2,
        presentation_epoch=2,
        superseded_epoch=1,
        reason="attempt_restarted",
    )))
    reducer.apply(parse_stream_event(_payload(
        "text.delta",
        event_seq=3,
        presentation_epoch=2,
        segment_id="new_item",
        text="new",
    )))

    assert reducer.assistant_text == "new"
    assert [item.item_id for item in reducer.canonical_items] == ["new_item"]
    old_item, new_item = reducer.item_history
    assert old_item.superseded is True
    assert old_item.superseded_by_epoch == 2
    assert new_item.superseded is False


def test_reducer_projects_tool_call_and_output_as_distinct_items() -> None:
    reducer = _reducer()
    call = parse_stream_event(_payload(
        "tool.call",
        event_seq=1,
        item_id="call_test",
        item_kind="tool_call",
        item_status="waiting_result",
        call_id="call_test",
        name="read_file",
        arguments={"path": "README.md"},
    ))
    output = parse_stream_event(_payload(
        "tool.output",
        event_seq=2,
        item_id="call_test:output",
        item_kind="tool_output",
        item_status="completed",
        call_id="call_test",
        name="read_file",
        arguments={"path": "README.md"},
        status="completed",
        result={"text": "contents"},
    ))

    reducer.apply(call)
    reducer.apply(output)

    call_item, output_item = reducer.canonical_items
    assert (call_item.item_id, call_item.item_status) == (
        "call_test",
        "waiting_result",
    )
    assert call_item.payload_value()["arguments"] == {"path": "README.md"}
    assert (output_item.item_id, output_item.item_status) == (
        "call_test:output",
        "completed",
    )
    assert output_item.payload_value()["result"] == {"text": "contents"}


def test_reducer_merges_builtin_tool_lifecycle() -> None:
    reducer = _reducer()
    reducer.apply(parse_stream_event(_payload(
        "tool.builtin.call",
        event_seq=1,
        item_id="builtin_test",
        item_kind="builtin_tool",
        item_status="in_progress",
        builtin_call_id="builtin_test",
        builtin_type="web_search_call",
        status="in_progress",
        queries=["protocol"],
    )))
    completed = reducer.apply(parse_stream_event(_payload(
        "tool.builtin.done",
        event_seq=2,
        item_id="builtin_test",
        item_kind="builtin_tool",
        item_status="completed",
        builtin_call_id="builtin_test",
        builtin_type="web_search_call",
        status="completed",
        sources=[{"url": "https://example.com"}],
        source_count=1,
    )))

    assert completed is not None
    assert completed.item_status == "completed"
    assert completed.payload_value()["sources"] == [
        {"url": "https://example.com"},
    ]


def test_reducer_aggregates_active_text_and_builtin_sources() -> None:
    reducer = _reducer()
    reducer.apply(parse_stream_event(_payload(
        "tool.builtin.done",
        event_seq=1,
        item_id="builtin_test",
        item_kind="builtin_tool",
        item_status="completed",
        builtin_call_id="builtin_test",
        builtin_type="web_search_call",
        status="completed",
        sources=[{"url": "https://example.com/tool"}],
        source_count=1,
    )))
    reducer.apply(parse_stream_event(_payload(
        "text.delta",
        event_seq=2,
        segment_id="segment_test",
        text="answer",
    )))
    reducer.apply(parse_stream_event(_payload(
        "text.meta",
        event_seq=3,
        segment_id="segment_test",
        sources=[
            {"url": "https://example.com/tool"},
            {"url": "https://example.com/text"},
        ],
        source_count=2,
    )))

    assert reducer.sources == (
        {"url": "https://example.com/tool"},
        {"url": "https://example.com/text"},
    )


def test_reducer_excludes_sources_from_superseded_presentation() -> None:
    reducer = _reducer()
    reducer.apply(parse_stream_event(_payload(
        "text.meta",
        event_seq=1,
        presentation_epoch=1,
        segment_id="old_item",
        sources=[{"url": "https://old.example"}],
        source_count=1,
    )))
    reducer.apply(parse_stream_event(_payload(
        "text.delta",
        event_seq=2,
        presentation_epoch=1,
        segment_id="old_item",
        text="old",
    )))
    reducer.apply(parse_stream_event(_payload(
        "presentation.superseded",
        event_seq=3,
        presentation_epoch=2,
        superseded_epoch=1,
        reason="attempt_restarted",
    )))
    reducer.apply(parse_stream_event(_payload(
        "text.meta",
        event_seq=4,
        presentation_epoch=2,
        segment_id="new_item",
        sources=[{"url": "https://new.example"}],
        source_count=1,
    )))
    reducer.apply(parse_stream_event(_payload(
        "text.delta",
        event_seq=5,
        presentation_epoch=2,
        segment_id="new_item",
        text="new",
    )))

    assert reducer.sources == ({"url": "https://new.example"},)


def test_approval_snapshot_precedes_replayed_pending_event() -> None:
    reducer = _reducer()
    pending = reducer.apply_approval_snapshot(_approval_snapshot(
        "pending",
        last_event_seq=4,
    ))

    assert [item.item_id for item in pending] == ["approval_test"]
    replayed = reducer.apply(_approval_event(event_seq=2))
    assert replayed is not None
    assert replayed.item_status == "waiting_approval"

    assert reducer.apply_approval_snapshot(_approval_snapshot(
        "resolved",
        last_event_seq=5,
    )) == ()
    replayed_after_resolution = reducer.apply(_approval_event(event_seq=2))

    assert replayed_after_resolution is not None
    assert replayed_after_resolution.item_status == "completed"
    assert reducer.pending_approval_items == ()
    assert reducer.canonical_items[0].payload_value()["snapshot_status"] == (
        "resolved"
    )


if __name__ == '__main__':
    pass
