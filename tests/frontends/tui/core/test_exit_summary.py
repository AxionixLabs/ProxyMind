"""核对冻结事实到退出文本的计数、状态和样式。"""

from dataclasses import replace

import pytest

from agent.application.views.context_usage import SessionExitSnapshot
from agent.protocol.context_usage import (
    ContextUsageRecord,
    SessionTokenUsageRecord,
)
from frontends.tui.core.styles import exit_summary_fragments


def snapshot():
    usage = SessionTokenUsageRecord(17_677, 17_663, 14_976, 0, 14, 0, 1, 0)
    record = ContextUsageRecord("cid_test", "sid_test", "turn_test", 4, 1,
                                100_000, 56_000, usage, "provider", "test-model", "responses")
    return SessionExitSnapshot("cid_test", "sid_test", "recoverable", record, True)


def text(value):
    return "".join(part for _style, part in exit_summary_fragments(value))


def test_usage_and_resume_follow_codex_layout_with_square_prefix():
    assert text(snapshot()) == (
        "■ Token usage: total=2,701 input=2,687 (+ 14,976 cached) output=14\n"
        "■ To continue this session, run:\n  mind resume sid_test"
    )


@pytest.mark.parametrize("changes, expected", [
    ({"reasoning_output_tokens": 7}, "output=14 (reasoning 7)"),
    ({"cached_input_tokens": 0}, "total=17,677 input=17,663 output=14"),
    ({"reasoning_output_tokens": None, "cache_write_input_tokens": None}, "output=14"),
    ({"total_tokens": 14_976, "input_tokens": 14_976, "output_tokens": 0},
     "total=0 input=0 (+ 14,976 cached) output=0"),
])
def test_usage_uses_only_confirmed_details(changes, expected):
    value = snapshot()
    usage = replace(value.record.total_token_usage, **changes)
    rendered = text(replace(value, record=replace(value.record, total_token_usage=usage)))
    assert rendered.splitlines()[0].endswith(expected)


@pytest.mark.parametrize("changes", [
    {"unreported_calls": 1}, {"input_tokens": None}, {"cached_input_tokens": None},
    {"output_tokens": None},
    {"total_tokens": 0, "input_tokens": 0, "cached_input_tokens": 0, "output_tokens": 0},
])
def test_partial_or_zero_totals_do_not_hide_reliable_resume(changes):
    value = snapshot()
    usage = replace(value.record.total_token_usage, **changes)
    rendered = text(replace(value, record=replace(value.record, total_token_usage=usage)))
    assert rendered == "■ To continue this session, run:\n  mind resume sid_test"


@pytest.mark.parametrize("disposition, ending", [
    ("deleted", "output=14"),
    ("archived", "■ Session archived: sid_test"),
    ("pending_delete", "■ Session deletion is pending. In a new session, run:\n  /delete recover delete_test"),
])
def test_unavailable_sessions_keep_usage_and_applicable_guidance(disposition, ending):
    value = replace(snapshot(), disposition=disposition,
                    deletion_request_id="delete_test" if disposition == "pending_delete" else None)
    rendered = text(value)
    assert rendered.startswith("■ Token usage: total=2,701")
    assert rendered.endswith(ending)
    assert "mind resume" not in rendered


def test_unconfirmed_remote_stop_uses_observed_usage_label():
    rendered = text(replace(snapshot(), remote_stop_confirmed=False))
    assert rendered.startswith("■ Token usage so far: total=2,701")
    assert "■ Remote work may still be running.\n■ To continue this session, run:" in rendered


def test_deleted_session_without_usage_is_silent():
    assert text(replace(snapshot(), disposition="deleted", record=None)) == ""
