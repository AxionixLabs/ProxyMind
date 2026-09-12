import json
from dataclasses import replace
from pathlib import Path

import pytest

from agent.adapters.protocol.context_usage import context_usage_record
from agent.application.turns.context_usage import ContextUsageProjection
from agent.application.views.context_usage import context_remaining_percent
from protocol.schema.stream_events import (
    ContextUsageUpdatedEvent,
    parse_stream_event,
)


@pytest.fixture
def record(fixtures_root: Path):
    fixture = json.loads((
        fixtures_root / "protocol/context_usage.json"
    ).read_text(encoding="utf-8"))
    event = parse_stream_event(fixture["event"])
    assert isinstance(event, ContextUsageUpdatedEvent)
    return context_usage_record(event)


def test_percentage_uses_independent_contract_examples(record, fixtures_root: Path) -> None:
    fixture = json.loads((
        fixtures_root / "protocol/context_usage.json"
    ).read_text(encoding="utf-8"))
    for window, used, expected in fixture["percentage_examples"]:
        assert context_remaining_percent(replace(
            record, model_context_window=window, last_total_tokens=used,
        )) == expected
    assert context_remaining_percent(replace(record, total_tokens=999_999_999)) == 91
    assert context_remaining_percent(replace(
        record, last_total_tokens=None, usage_source="unknown",
    )) is None


def test_projection_replaces_whole_snapshots_and_isolates_sessions(record) -> None:
    projection = ContextUsageProjection()
    projection.activate(record.cid, record.sid, initial=True)
    assert projection.view.status == "initial"
    projection.mark_started()
    assert projection.view.status == "unknown"
    assert projection.apply(record)
    assert not projection.apply(record)
    assert not projection.apply(replace(record, event_seq=10, last_total_tokens=1))
    assert not projection.apply(replace(record, cid="other", event_seq=99))
    assert not projection.apply(replace(record, sid="review", event_seq=99))
    changed_model = replace(record, event_seq=13, model="small-model", model_context_window=20_000)
    assert projection.apply(changed_model)
    assert projection.view.record == changed_model
    assert context_remaining_percent(projection.view.record) == 0
    projection.activate(record.cid, "fork", initial=False)
    projection.finish_replay(record.cid, "fork")
    assert projection.view.status == "unknown"
    assert projection.view.record is None


def test_replay_publishes_once_at_confirmed_boundary_and_unsubscribes(record) -> None:
    projection = ContextUsageProjection()
    projection.activate(record.cid, record.sid, initial=False)
    views = []
    unsubscribe = projection.subscribe(views.append)
    assert projection.apply(record)
    compacted = replace(record, event_seq=20, last_total_tokens=13_000, usage_source="estimate")
    projection.apply(compacted)
    assert [view.status for view in views] == ["pending"]
    projection.finish_replay(record.cid, "other")
    assert len(views) == 1
    projection.finish_replay(record.cid, record.sid)
    assert [view.status for view in views] == ["pending", "known"]
    assert views[-1].record.total_tokens == 250_000
    assert views[-1].record.last_total_tokens == 13_000
    projection.begin_replay(record.cid, record.sid)
    projection.apply(record)
    projection.finish_replay(record.cid, record.sid)
    assert views[-1].record == compacted
    unsubscribe()
    unsubscribe()
    count = len(views)
    projection.close()
    assert len(views) == count


def test_unknown_update_replaces_previously_known_usage(record) -> None:
    projection = ContextUsageProjection()
    projection.activate(record.cid, record.sid, initial=True)
    projection.apply(record)
    projection.apply(replace(record, event_seq=13, last_total_tokens=None, usage_source="unknown"))
    assert projection.view.status == "unknown"


def test_retained_prefix_does_not_restore_outdated_snapshot(record) -> None:
    projection = ContextUsageProjection()
    projection.activate(record.cid, record.sid, initial=True)
    projection.apply(record)
    projection.begin_replay(record.cid, record.sid)
    projection.discard_retained_prefix(record.cid, record.sid, record.event_seq + 10)
    assert not projection.apply(record)
    projection.finish_replay(record.cid, record.sid)
    assert projection.view.status == "unknown"
    newer = replace(record, event_seq=30)
    projection.apply(newer)
    projection.discard_retained_prefix(record.cid, record.sid, 20)
    assert projection.view.record == newer
    projection.activate(record.cid, "other", initial=True)
    assert projection.apply(replace(record, sid="other"))


def test_retained_authoritative_snapshot_can_restore_before_floor(record):
    projection = ContextUsageProjection()
    projection.activate(record.cid, record.sid, initial=False)
    projection.discard_retained_prefix(record.cid, record.sid, 100)
    assert not projection.apply(record)
    projection.restore(record.cid, record.sid, record)
    assert projection.view.status == 'pending'
    projection.finish_replay(record.cid, record.sid)
    assert projection.view.record == record
