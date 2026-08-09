# -*- coding: utf-8 -*-

import asyncio
from unittest.mock import (
    Mock,
    patch,
)

import pytest

import mind_app.tui.adapters.hooks as hook_adapter_module
from mind_app.runtime.hooks.models import (
    HookOutputEntry,
    HookRunSummary,
)
from mind_app.tui.adapters.hooks import (
    TuiHookStatusAdapter,
    _HookDisplayState,
)
from mind_app.tui.core.models import FragmentBlock
from mind_app.tui.core.keymap import TuiRuntimeKeymap
from mind_app.tui.core.render import (
    display_line_count,
    fragments_text,
)
from mind_app.tui.core.runtime import TuiRuntime
from mind_app.tui.core.screen import FrameGeometry


def _run(
    run_id: str,
    *,
    event: str = "PreToolUse",
    status: str = "running",
    status_message: str = "",
    entries: tuple[HookOutputEntry, ...] = (),
) -> HookRunSummary:
    return HookRunSummary(
        id=run_id,
        hook_key="same-hook",
        event=event,
        status=status,
        status_message=status_message,
        entries=entries,
    )


def _overlay_text(runtime: TuiRuntime) -> str:
    return fragments_text(runtime.screen.transcript_overlay.fragments())


def test_hook_display_state_hides_quick_quiet_success() -> None:
    state = _HookDisplayState()
    state.start(_run("quick"), now=10.0)

    persistent = state.complete(
        _run("quick", status="completed"),
        now=10.1,
    )

    assert persistent is None
    assert not state.visible
    assert state.next_deadline() is None
    assert state.snapshot() == {"groups": ()}


def test_hook_display_state_lingers_after_visible_quiet_success() -> None:
    state = _HookDisplayState()
    state.start(_run("slow", event="SessionStart"), now=20.0)

    assert state.advance(now=20.3)
    assert state.visible

    persistent = state.complete(
        _run("slow", event="SessionStart", status="completed"),
        now=20.4,
    )

    assert persistent is None
    assert state.visible
    removal_deadline = state.next_deadline()
    assert removal_deadline == pytest.approx(20.9)
    assert not state.advance(now=20.899)
    assert state.visible
    assert removal_deadline is not None
    assert state.advance(now=removal_deadline)
    assert not state.visible


def test_hook_display_state_groups_adjacent_runs_without_losing_ids() -> None:
    state = _HookDisplayState()
    state.start(_run("first", status_message="Checking"), now=30.0)
    state.start(_run("second", status_message="Checking"), now=30.0)
    state.start(_run(
        "third",
        event="PostToolUse",
        status_message="Reviewing",
    ), now=30.0)
    state.advance(now=30.3)

    assert state.snapshot() == {
        "groups": (
            {
                "event": "PreToolUse",
                "status_message": "Checking",
                "count": 2,
            },
            {
                "event": "PostToolUse",
                "status_message": "Reviewing",
                "count": 1,
            },
        ),
    }

    state.complete(
        _run("second", status="completed", status_message="Checking"),
        now=31.0,
    )

    assert state.snapshot()["groups"][0]["count"] == 1


def test_hook_display_state_persists_failure_before_reveal() -> None:
    state = _HookDisplayState()
    state.start(_run("failed"), now=40.0)
    completed = _run(
        "failed",
        status="failed",
        entries=(HookOutputEntry("error", "policy crashed"),),
    )

    assert state.complete(completed, now=40.1) is completed
    assert not state.visible
    assert state.next_deadline() is None


def test_hook_display_state_keeps_other_run_visible_on_persistent_completion(
) -> None:
    state = _HookDisplayState()
    state.start(_run("first", status_message="Checking"), now=50.0)
    state.start(_run("second", status_message="Checking"), now=50.0)
    state.advance(now=50.3)
    failed = _run(
        "first",
        status="failed",
        status_message="Checking",
        entries=(HookOutputEntry("error", "policy crashed"),),
    )

    assert state.complete(failed, now=50.4) is failed
    assert state.visible
    assert state.snapshot() == {
        "groups": ({
            "event": "PreToolUse",
            "status_message": "Checking",
            "count": 1,
        },),
    }


@pytest.mark.anyio
async def test_tui_hook_adapter_delays_and_lingers_quiet_status(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        hook_adapter_module,
        "HOOK_RUN_REVEAL_DELAY_SEC",
        0.01,
    )
    monkeypatch.setattr(
        hook_adapter_module,
        "QUIET_HOOK_MIN_VISIBLE_SEC",
        0.04,
    )
    runtime = TuiRuntime()
    adapter = TuiHookStatusAdapter(runtime)

    try:
        started = _run("quiet", event="SessionStart")
        await adapter.started(started)

        assert runtime.screen.activity_block is None
        assert runtime.screen._status_height() == 0

        await asyncio.sleep(0.02)

        active = fragments_text(runtime.screen.activity_block.fragments)
        assert "Running SessionStart hook" in active
        assert runtime.screen._status_height() == 1

        await adapter.completed(_run(
            "quiet",
            event="SessionStart",
            status="completed",
        ))

        assert runtime.screen.activity_block is not None
        assert runtime.document.blocks == []

        await asyncio.sleep(0.05)

        assert runtime.screen.activity_block is None
        assert runtime.screen._status_height() == 0
        assert runtime.document.blocks == []
    finally:
        await runtime.close()


@pytest.mark.anyio
async def test_pending_hook_only_enters_overlay_after_reveal(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        hook_adapter_module,
        "HOOK_RUN_REVEAL_DELAY_SEC",
        0.01,
    )
    monkeypatch.setattr(
        hook_adapter_module,
        "QUIET_HOOK_MIN_VISIBLE_SEC",
        0.03,
    )
    runtime = TuiRuntime()
    adapter = TuiHookStatusAdapter(runtime)
    runtime.append_block(FragmentBlock((("", "stable"),)), kind="assistant")

    try:
        await adapter.started(_run("quiet", event="SessionStart"))
        runtime.toggle_transcript_overlay()

        assert _overlay_text(runtime) == "stable"
        assert runtime.screen.activity_transcript_block is None
        assert runtime.document.transcript_snapshot().live_tail is None

        await asyncio.sleep(0.02)

        visible = _overlay_text(runtime)
        assert visible.startswith("stable\n\n")
        assert "Running SessionStart hook" in visible
        assert runtime.document.transcript_snapshot().live_tail is None
        composed = runtime.screen._transcript_snapshot()
        assert composed.live_tail is not None
        assert len(composed.live_tail.cells) == 1

        await adapter.completed(_run(
            "quiet",
            event="SessionStart",
            status="completed",
        ))
        await asyncio.sleep(0.04)

        assert _overlay_text(runtime) == "stable"
        assert runtime.screen.activity_transcript_block is None
        assert len(runtime.document.blocks) == 1
    finally:
        await runtime.close()


@pytest.mark.anyio
async def test_hook_overlay_tail_excludes_other_activity_slots() -> None:
    runtime = TuiRuntime()
    runtime.append_block(FragmentBlock((("", "stable"),)), kind="assistant")
    runtime.set_active_renderable(
        FragmentBlock((("", "assistant live"),)),
        kind="assistant",
    )

    try:
        await runtime.begin_wait_status()
        await runtime.begin_operation_status(
            lambda: {"summary": "Foreground operation"},
        )
        await runtime.transition_hook_status(
            snapshot=lambda: {
                "groups": ({
                    "event": "PreToolUse",
                    "status_message": "Checking",
                    "count": 1,
                },),
            },
            visible=True,
        )
        runtime.toggle_transcript_overlay()

        text = _overlay_text(runtime)
        assert text.startswith("stable\n\nassistant live\n\n")
        assert "Running PreToolUse hook: Checking" in text
        assert "Thinking" not in text
        assert "Foreground operation" not in text
        assert text.count("\n\n") == 2

        document_tail = runtime.document.transcript_snapshot().live_tail
        composed_tail = runtime.screen._transcript_snapshot().live_tail
        assert document_tail is not None
        assert composed_tail is not None
        assert len(document_tail.cells) == 1
        assert len(composed_tail.cells) == 2
    finally:
        await runtime.close()


@pytest.mark.anyio
async def test_hook_completion_replaces_overlay_tail_without_duplication() -> None:
    runtime = TuiRuntime()
    runtime.append_block(FragmentBlock((("", "stable"),)), kind="assistant")
    snapshot = lambda: {
        "groups": ({
            "event": "PreToolUse",
            "status_message": "Checking",
            "count": 1,
        },),
    }

    try:
        await runtime.transition_hook_status(snapshot=snapshot, visible=True)
        runtime.toggle_transcript_overlay()
        assert "Running PreToolUse hook" in _overlay_text(runtime)

        invalidate = Mock()
        runtime.screen._invalidate_now = invalidate
        await runtime.transition_hook_status(
            snapshot=snapshot,
            visible=False,
            completed_block=FragmentBlock((("", "hook failed"),)),
            transcript_block=FragmentBlock((("", "hook failed"),)),
            raw_text="hook failed",
        )

        assert invalidate.call_count == 1
        assert _overlay_text(runtime) == "stable\n\nhook failed"
        assert runtime.screen.activity_transcript_block is None
        assert runtime.document.transcript_snapshot().live_tail is None
        assert runtime.screen.transcript_overlay._cached_live_tail_key is None
        assert len(runtime.document.blocks) == 2
    finally:
        await runtime.close()


@pytest.mark.anyio
async def test_turn_finish_clears_hook_linger_without_delayed_frame(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        hook_adapter_module,
        "HOOK_RUN_REVEAL_DELAY_SEC",
        0.01,
    )
    monkeypatch.setattr(
        hook_adapter_module,
        "QUIET_HOOK_MIN_VISIBLE_SEC",
        0.08,
    )
    runtime = TuiRuntime()
    adapter = TuiHookStatusAdapter(runtime)

    try:
        runtime.set_execution_active(True)
        await runtime.begin_operation_status(
            lambda: {"summary": "Foreground operation"},
        )
        await adapter.started(_run(
            "old",
            event="PostToolUse",
            status_message="Old turn",
        ))
        await asyncio.sleep(0.02)
        await adapter.completed(_run(
            "old",
            event="PostToolUse",
            status="completed",
            status_message="Old turn",
        ))
        runtime.toggle_transcript_overlay()

        assert "Old turn" in _overlay_text(runtime)
        assert adapter._timer_task is not None

        invalidate = Mock()
        runtime.screen._invalidate_now = invalidate
        runtime.set_execution_active(False)

        assert invalidate.call_count == 1
        assert adapter.snapshot() == {"groups": ()}
        assert adapter._timer_task is None
        assert runtime.screen.activity_transcript_block is None
        assert "Old turn" not in _overlay_text(runtime)
        remaining = fragments_text(runtime.screen.activity_block.fragments)
        assert "Foreground operation" in remaining
        assert "Old turn" not in remaining

        invalidate.reset_mock()
        await asyncio.sleep(0.09)
        invalidate.assert_not_called()

        runtime.set_execution_active(True)
        await adapter.started(_run(
            "new",
            event="PreToolUse",
            status_message="New turn",
        ))
        await asyncio.sleep(0.02)

        next_turn = _overlay_text(runtime)
        assert "New turn" in next_turn
        assert "Old turn" not in next_turn
    finally:
        runtime.set_execution_active(False)
        await runtime.close()


def test_hook_projection_refresh_only_rebuilds_overlay_live_tail() -> None:
    runtime = TuiRuntime()
    runtime.append_block(FragmentBlock((("", "stable"),)), kind="assistant")
    runtime.screen.set_activity_transcript_projection(
        FragmentBlock((("class:hook", "Hook frame one"),))
    )
    runtime.toggle_transcript_overlay()
    overlay = runtime.screen.transcript_overlay
    overlay.fragments()
    first_key = overlay._cached_live_tail_key

    with patch.object(
        overlay,
        "_rebuild_stable_index",
        wraps=overlay._rebuild_stable_index,
    ) as rebuild:
        runtime.screen.set_activity_transcript_projection(
            FragmentBlock((("class:hook", "Hook frame two"),))
        )
        rich_text = _overlay_text(runtime)

    second_key = overlay._cached_live_tail_key
    assert "Hook frame two" in rich_text
    assert "Hook frame one" not in rich_text
    assert first_key is not None
    assert second_key is not None
    assert first_key != second_key
    rebuild.assert_not_called()

    overlay.toggle_raw_mode()
    assert _overlay_text(runtime) == "stable\n\nHook frame two"


@pytest.mark.anyio
async def test_tui_hook_adapter_uses_slot_independent_from_operation(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        hook_adapter_module,
        "HOOK_RUN_REVEAL_DELAY_SEC",
        0.01,
    )
    monkeypatch.setattr(
        hook_adapter_module,
        "QUIET_HOOK_MIN_VISIBLE_SEC",
        0.02,
    )
    runtime = TuiRuntime()
    adapter = TuiHookStatusAdapter(runtime)

    try:
        await runtime.begin_operation_status(
            lambda: {"summary": "Foreground operation"},
        )
        await adapter.started(_run(
            "hook",
            status_message="Checking policy",
        ))
        await asyncio.sleep(0.02)

        active = fragments_text(runtime.screen.activity_block.fragments)
        assert "Foreground operation" in active
        assert "Running PreToolUse hook: Checking policy" in active
        assert "\n" in active

        await adapter.completed(_run(
            "hook",
            status="completed",
            status_message="Checking policy",
        ))
        await asyncio.sleep(0.03)

        remaining = fragments_text(runtime.screen.activity_block.fragments)
        assert "Foreground operation" in remaining
        assert "PreToolUse" not in remaining
    finally:
        await runtime.close()


@pytest.mark.anyio
async def test_tui_hook_adapter_restores_slot_after_external_activity_clear(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        hook_adapter_module,
        "HOOK_RUN_REVEAL_DELAY_SEC",
        0.01,
    )
    runtime = TuiRuntime()
    adapter = TuiHookStatusAdapter(runtime)

    try:
        await adapter.started(_run("first", status_message="First"))
        await asyncio.sleep(0.02)
        assert runtime.screen.activity_block is not None

        await runtime.end_activity_status(None, settle=False)
        assert runtime.screen.activity_block is None

        await adapter.started(_run("second", status_message="Second"))
        await asyncio.sleep(0.02)

        restored = fragments_text(runtime.screen.activity_block.fragments)
        assert "Running PreToolUse hook: First" in restored
        assert "Running PreToolUse hook: Second" in restored
    finally:
        await runtime.close()


@pytest.mark.anyio
async def test_tui_hook_adapter_persists_failure_and_full_context() -> None:
    runtime = TuiRuntime()
    adapter = TuiHookStatusAdapter(runtime)
    context = "\n".join(f"context line {index} " + "x" * 50 for index in range(8))
    completed = _run(
        "failed",
        event="PostToolUse",
        status="failed",
        entries=(
            HookOutputEntry("context", context),
            HookOutputEntry("error", "review failed"),
        ),
    )

    try:
        await adapter.completed(completed)

        assert runtime.screen.activity_block is None
        assert len(runtime.document.blocks) == 1

        block = runtime.document.blocks[0]
        display = fragments_text(block.display_block.fragments)
        transcript = fragments_text(block.transcript_block.fragments)

        assert display.startswith("• PostToolUse hook (failed)")
        assert "Ctrl+T to view transcript" in display
        assert "context line 7" not in display
        assert "context line 7" in transcript
        assert "error: review failed" in transcript
        assert "fg:#FF6B6B" in block.display_block.fragments[0][0]
        assert block.raw_text == transcript
    finally:
        await runtime.close()


@pytest.mark.anyio
async def test_tui_hook_adapter_limits_context_preview_to_three_rows() -> None:
    runtime = TuiRuntime()
    adapter = TuiHookStatusAdapter(runtime)
    width = 32
    runtime.screen._frame_geometry = FrameGeometry(width, 24, 1)
    completed = _run(
        "context",
        event="SessionStart",
        status="completed",
        entries=(HookOutputEntry(
            "context",
            "context words " * 30 + "tail marker",
        ),),
    )

    try:
        await adapter.completed(completed)

        block = runtime.document.blocks[0]
        display = fragments_text(block.display_block.fragments)
        transcript = fragments_text(block.transcript_block.fragments)

        assert display_line_count(display, width=width) == 4
        assert "tail marker" not in display
        assert "tail marker" in transcript
    finally:
        await runtime.close()


@pytest.mark.anyio
async def test_tui_hook_adapter_uses_configured_transcript_key_hint() -> None:
    runtime = TuiRuntime(keymap=TuiRuntimeKeymap.from_config({
        "tui": {
            "keymap": {
                "global": {"open_transcript": "f12"},
            },
        },
    }))
    adapter = TuiHookStatusAdapter(runtime)
    runtime.screen._frame_geometry = FrameGeometry(48, 24, 1)
    completed = _run(
        "context-key",
        status="completed",
        entries=(HookOutputEntry("context", "context " * 50),),
    )

    try:
        await adapter.completed(completed)

        display = fragments_text(
            runtime.document.blocks[0].display_block.fragments
        )
        assert "F12 to view transcript" in display
        assert "Ctrl+T" not in display
    finally:
        await runtime.close()


@pytest.mark.anyio
async def test_hook_status_transition_requests_one_atomic_frame() -> None:
    runtime = TuiRuntime()
    snapshot = lambda: {
        "groups": ({
            "event": "PreToolUse",
            "status_message": "Checking",
            "count": 1,
        },),
    }

    try:
        await runtime.transition_hook_status(
            snapshot=snapshot,
            visible=True,
        )
        invalidate = Mock()
        runtime.screen._invalidate_now = invalidate

        await runtime.transition_hook_status(
            snapshot=snapshot,
            visible=False,
            completed_block=FragmentBlock((("", "hook failed"),)),
            transcript_block=FragmentBlock((("", "hook failed"),)),
            raw_text="hook failed",
        )

        assert invalidate.call_count == 1
        assert runtime.screen.activity_block is None
        assert fragments_text(
            runtime.document.blocks[-1].display_block.fragments
        ) == "hook failed"
    finally:
        await runtime.close()
