# -*- coding: utf-8 -*-

from frontends.tui.core.stream_chunking import (
    StreamChunkingMode,
    StreamChunkingPolicy,
    StreamQueueSnapshot,
)


def _snapshot(
    pending_rows: int,
    oldest_age_sec: float | None,
) -> StreamQueueSnapshot:
    return StreamQueueSnapshot(
        pending_rows=pending_rows,
        oldest_age_sec=oldest_age_sec,
    )


def test_stream_chunking_stays_smooth_below_enter_thresholds() -> None:
    policy = StreamChunkingPolicy()

    decision = policy.decide(_snapshot(7, 0.119), now=1.0)

    assert decision.mode is StreamChunkingMode.SMOOTH
    assert decision.row_count == 1
    assert not decision.entered_catch_up


def test_stream_chunking_enters_catch_up_on_depth_or_age() -> None:
    depth_policy = StreamChunkingPolicy()
    age_policy = StreamChunkingPolicy()

    depth = depth_policy.decide(_snapshot(8, 0.01), now=1.0)
    age = age_policy.decide(_snapshot(2, 0.12), now=1.0)

    assert depth.mode is StreamChunkingMode.CATCH_UP
    assert depth.row_count == 8
    assert depth.entered_catch_up
    assert age.mode is StreamChunkingMode.CATCH_UP
    assert age.row_count == 2
    assert age.entered_catch_up


def test_stream_chunking_exits_only_after_low_pressure_hold() -> None:
    policy = StreamChunkingPolicy()

    policy.decide(_snapshot(8, 0.01), now=1.0)
    before_hold = policy.decide(_snapshot(2, 0.04), now=1.20)
    after_hold = policy.decide(_snapshot(2, 0.04), now=1.46)

    assert before_hold.mode is StreamChunkingMode.CATCH_UP
    assert before_hold.row_count == 2
    assert after_hold.mode is StreamChunkingMode.SMOOTH
    assert after_hold.row_count == 1


def test_stream_chunking_holds_reentry_until_cooldown_expires() -> None:
    policy = StreamChunkingPolicy()

    policy.decide(_snapshot(8, 0.01), now=1.0)
    idle = policy.decide(_snapshot(0, None), now=1.02)
    held = policy.decide(_snapshot(8, 0.01), now=1.12)
    reentered = policy.decide(_snapshot(8, 0.01), now=1.32)

    assert idle.mode is StreamChunkingMode.SMOOTH
    assert idle.row_count == 0
    assert held.mode is StreamChunkingMode.SMOOTH
    assert held.row_count == 1
    assert reentered.mode is StreamChunkingMode.CATCH_UP
    assert reentered.row_count == 8


def test_stream_chunking_severe_backlog_bypasses_reentry_hold() -> None:
    policy = StreamChunkingPolicy()

    policy.decide(_snapshot(8, 0.01), now=1.0)
    policy.decide(_snapshot(0, None), now=1.02)
    severe = policy.decide(_snapshot(64, 0.01), now=1.12)

    assert severe.mode is StreamChunkingMode.CATCH_UP
    assert severe.row_count == 64
    assert severe.entered_catch_up


def test_stream_chunking_reset_clears_cross_stream_hysteresis() -> None:
    policy = StreamChunkingPolicy()

    policy.decide(_snapshot(8, 0.01), now=1.0)
    policy.reset()
    decision = policy.decide(_snapshot(8, 0.01), now=1.01)

    assert decision.mode is StreamChunkingMode.CATCH_UP
    assert decision.row_count == 8
    assert decision.entered_catch_up
