import json
from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from agent.adapters.protocol.context_usage import context_usage_record
from agent.adapters.protocol.items import CanonicalItemReducer
from agent.application.views.context_usage import context_remaining_percent
from protocol.schema.context_usage import CONTEXT_USAGE_FIELDS
from protocol.schema.stream_events import (
    ContextUsageUpdatedEvent,
    parse_compact_event,
    parse_stream_event,
)


@pytest.fixture
def payload(fixtures_root: Path):
    return json.loads((
        fixtures_root / "protocol/context_usage.json"
    ).read_text(encoding="utf-8"))["event"]


def test_context_usage_is_immutable_and_does_not_create_an_item(payload) -> None:
    event = parse_stream_event(payload)
    assert isinstance(event, ContextUsageUpdatedEvent)
    payload["last_token_usage"]["total_tokens"] = 0
    assert event.snapshot.last_token_usage.total_tokens == 20_000
    with pytest.raises(FrozenInstanceError):
        event.snapshot.model_context_window = 1
    with pytest.raises(FrozenInstanceError):
        event.snapshot.last_token_usage.total_tokens = 1
    reducer = CanonicalItemReducer(cid=event.cid, sid=event.sid, turn_id=event.turn_id)
    assert reducer.apply(event) is None
    assert reducer.canonical_items == ()
    record = context_usage_record(event)
    assert record.total_tokens == 250_000
    assert context_remaining_percent(record) == 91
    assert parse_compact_event({**payload, "last_token_usage": {"total_tokens": 20_000}}) == event


@pytest.mark.parametrize("field", sorted(CONTEXT_USAGE_FIELDS))
def test_context_usage_requires_all_snapshot_fields(payload, field) -> None:
    del payload[field]
    with pytest.raises(ValueError):
        parse_stream_event(payload)


@pytest.mark.parametrize("value", [True, False, 2.0, "100000", -1, 0, 1, {}, []])
def test_context_usage_rejects_invalid_window(payload, value) -> None:
    payload["model_context_window"] = value
    with pytest.raises(ValueError, match="model_context_window"):
        parse_stream_event(payload)


@pytest.mark.parametrize("field", ["last_token_usage", "total_token_usage"])
@pytest.mark.parametrize("value", [True, False, 2.0, "2", -1, None, {}, []])
def test_context_usage_rejects_invalid_counts(payload, field, value) -> None:
    payload[field] = {"total_tokens": value}
    with pytest.raises(ValueError):
        parse_stream_event(payload)


@pytest.mark.parametrize("field", ["last_token_usage", "total_token_usage"])
@pytest.mark.parametrize("value", [{}, {"total_tokens": 2, "input_tokens": 1}, 10, "unknown"])
def test_context_usage_rejects_invalid_counter_structure(payload, field, value) -> None:
    payload[field] = value
    with pytest.raises(ValueError):
        parse_stream_event(payload)


@pytest.mark.parametrize("fields", [
    {"usage_source": "unknown"},
    {"last_token_usage": None},
    {"usage_source": "guessed"},
    {"usage_source": None},
    {"model": None},
    {"route": ""},
    {"display": {"text": "must not render"}},
    {"item_id": "unexpected"},
    {"usage": {"total_tokens": 10}},
    {"event_seq": True},
    {"event_seq": None},
])
def test_context_usage_rejects_inconsistent_or_undeclared_fields(payload, fields) -> None:
    with pytest.raises(ValueError):
        parse_stream_event({**payload, **fields})


def test_context_usage_accepts_explicit_unknown_without_zero_fallback(payload) -> None:
    event = parse_stream_event({
        **payload,
        "model_context_window": None,
        "last_token_usage": None,
        "total_token_usage": None,
        "usage_source": "unknown",
    })
    assert isinstance(event, ContextUsageUpdatedEvent)
    record = context_usage_record(event)
    assert record.last_total_tokens is None
    assert record.total_tokens is None
    assert context_remaining_percent(record) is None
