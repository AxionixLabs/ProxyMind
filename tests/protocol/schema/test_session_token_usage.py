import json
from dataclasses import (
    FrozenInstanceError,
    asdict,
)
from pathlib import Path

import pytest

from protocol.schema.context_usage import parse_context_usage


@pytest.fixture
def samples(fixtures_root: Path):
    return json.loads((
        fixtures_root / "protocol/session_token_usage.json"
    ).read_text(encoding="utf-8"))


def parse_usage(usage):
    return parse_context_usage({
        "model_context_window": 100000,
        "last_token_usage": {"total_tokens": 20000},
        "total_token_usage": usage,
        "usage_source": "provider",
        "model": "test-model",
        "route": "responses",
    }).total_token_usage


@pytest.mark.parametrize("name, complete", [
    ("complete", True), ("zero", True), ("unknown", None),
    ("partial_details", False), ("partial_calls", False), ("total_only", False),
])
def test_usage_preserves_complete_zero_unknown_and_partial(samples, name, complete):
    usage = parse_usage(samples[name])
    if complete is None:
        assert usage is None
    else:
        assert usage.is_complete is complete
        assert asdict(usage) == samples[name]


def test_display_counts_exclude_cache_and_do_not_add_reasoning(samples):
    usage = parse_usage(samples["complete"])
    assert usage.total_tokens == 17677
    assert usage.input_tokens - usage.cached_input_tokens == 2687
    assert usage.input_tokens - usage.cached_input_tokens + usage.output_tokens == 2701
    assert usage.reasoning_output_tokens == 4


@pytest.mark.parametrize("field", [
    "total_tokens", "input_tokens", "cached_input_tokens", "cache_write_input_tokens",
    "output_tokens", "reasoning_output_tokens", "reported_calls", "unreported_calls",
])
@pytest.mark.parametrize("value", [True, False, -1, 1.5, "2", {}, []])
def test_usage_rejects_invalid_counter_types(samples, field, value):
    with pytest.raises(ValueError):
        parse_usage({**samples["complete"], field: value})


@pytest.mark.parametrize("field", [
    "total_tokens", "input_tokens", "cached_input_tokens", "cache_write_input_tokens",
    "output_tokens", "reasoning_output_tokens", "reported_calls", "unreported_calls",
])
def test_usage_requires_even_nullable_fields(samples, field):
    payload = samples["complete"]
    del payload[field]
    with pytest.raises(ValueError):
        parse_usage(payload)


@pytest.mark.parametrize("changes", [
    {"total_tokens": None}, {"reported_calls": None}, {"unreported_calls": None},
    {"total_tokens": 17678}, {"cached_input_tokens": 17664},
    {"cache_write_input_tokens": 3000}, {"reasoning_output_tokens": 15},
    {"input_tokens": None, "output_tokens": 17678},
    {"input_tokens": None, "cache_write_input_tokens": 3000},
    {"output_tokens": None, "reasoning_output_tokens": 15},
    {"reported_calls": 0}, {"extra": 0},
])
def test_usage_rejects_inconsistent_or_undeclared_values(samples, changes):
    with pytest.raises(ValueError):
        parse_usage({**samples["complete"], **changes})


def test_optional_details_do_not_hide_complete_core_counts(samples):
    usage = parse_usage({
        **samples["complete"], "cache_write_input_tokens": None,
        "reasoning_output_tokens": None,
    })
    assert usage.is_complete


def test_empty_ledger_is_known_zero_and_missing_calls_stay_partial(samples):
    empty = {**samples["zero"], "reported_calls": 0}
    assert parse_usage(empty).is_complete
    assert not parse_usage({**empty, "unreported_calls": 1}).is_complete


def test_cumulative_usage_is_immutable(samples):
    usage = parse_usage(samples["complete"])
    with pytest.raises(FrozenInstanceError):
        usage.input_tokens = 0
