# -*- coding: utf-8 -*-

import pytest
from prompt_toolkit.utils import get_cwidth

from mind_app.runtime.hooks.models import (
    HookOutputEntry,
    HookRunSummary,
)
from mind_app.tui.adapters.hooks import TuiHookStatusAdapter
from mind_app.tui.core.keymap import TuiRuntimeKeymap
from mind_app.tui.core.models import FragmentBlock
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
    duration_ms: int | None = None,
    entries: tuple[HookOutputEntry, ...] = (),
) -> HookRunSummary:
    return HookRunSummary(
        id=run_id,
        hook_key="same-hook",
        event=event,
        status=status,
        status_message=status_message,
        duration_ms=duration_ms,
        entries=entries,
    )


def _block_text(runtime: TuiRuntime, index: int) -> str:
    return fragments_text(runtime.document.blocks[index].display_block.fragments)


@pytest.mark.anyio
async def test_tui_hook_adapter_prints_start_as_stable_operation() -> None:
    runtime = TuiRuntime()
    adapter = TuiHookStatusAdapter(runtime)

    try:
        await adapter.started(_run(
            "started",
            event="UserPromptSubmit",
            status_message="Checking prompt",
        ))

        assert runtime.screen.activity_block is None
        assert len(runtime.document.blocks) == 1
        assert runtime.document.blocks[0].kind == "operation"
        assert _block_text(runtime, 0) == (
            "• Running UserPromptSubmit hook: Checking prompt"
        )
        assert runtime.document.blocks[0].raw_text == (
            "• Running UserPromptSubmit hook: Checking prompt"
        )
        prefix_style = runtime.document.blocks[0].display_block.fragments[0][0]
        assert prefix_style == "bold fg:#7F8C9A"
        assert "dim" not in prefix_style
    finally:
        await runtime.close()


@pytest.mark.anyio
async def test_tui_hook_adapter_does_not_join_thinking_activity() -> None:
    runtime = TuiRuntime()
    adapter = TuiHookStatusAdapter(runtime)

    try:
        await runtime.begin_wait_status()
        await adapter.started(_run(
            "started",
            status_message="Checking command",
        ))

        activity = fragments_text(runtime.screen.activity_block.fragments)
        assert "Thinking" in activity
        assert "Hook" not in activity
        assert "Running PreToolUse hook" in _block_text(runtime, 0)
    finally:
        await runtime.close()


@pytest.mark.anyio
async def test_tui_hook_adapter_prints_quiet_success_result() -> None:
    runtime = TuiRuntime()
    adapter = TuiHookStatusAdapter(runtime)

    try:
        await adapter.started(_run(
            "quiet",
            event="UserPromptSubmit",
            status_message="Visible quiet prompt hook",
        ))
        await adapter.completed(_run(
            "quiet",
            event="UserPromptSubmit",
            status="completed",
            status_message="Visible quiet prompt hook",
            duration_ms=25,
        ))

        assert len(runtime.document.blocks) == 2
        assert _block_text(runtime, 0) == (
            "• Running UserPromptSubmit hook: Visible quiet prompt hook"
        )
        assert _block_text(runtime, 1) == (
            "• Ran UserPromptSubmit hook: Visible quiet prompt hook\n"
            "  └ completed · 25ms"
        )
        assert runtime.screen.activity_block is None

        runtime.toggle_transcript_overlay()
        assert fragments_text(runtime.screen.transcript_overlay.fragments()) == (
            "• Running UserPromptSubmit hook: Visible quiet prompt hook\n\n"
            "• Ran UserPromptSubmit hook: Visible quiet prompt hook\n"
            "  └ completed · 25ms"
        )
    finally:
        await runtime.close()


@pytest.mark.anyio
async def test_tui_hook_adapter_prints_each_concurrent_hook() -> None:
    runtime = TuiRuntime()
    adapter = TuiHookStatusAdapter(runtime)

    try:
        await adapter.started(_run("first", status_message="First policy"))
        await adapter.started(_run("second", status_message="Second policy"))
        await adapter.completed(_run(
            "second",
            status="completed",
            status_message="Second policy",
            duration_ms=1200,
        ))
        await adapter.completed(_run(
            "first",
            status="failed",
            status_message="First policy",
            duration_ms=32,
            entries=(HookOutputEntry("error", "First policy failed"),),
        ))

        assert len(runtime.document.blocks) == 4
        assert "First policy" in _block_text(runtime, 0)
        assert "Second policy" in _block_text(runtime, 1)
        assert _block_text(runtime, 2) == (
            "• Ran PreToolUse hook: Second policy\n"
            "  └ completed · 1.20s"
        )
        assert _block_text(runtime, 3) == (
            "• Ran PreToolUse hook: First policy\n"
            "  └ failed · 32ms\n"
            "    error: First policy failed"
        )

        runtime.toggle_transcript_overlay()
        transcript = fragments_text(
            runtime.screen.transcript_overlay.fragments()
        )
        assert transcript.count("\n\n") == 3
        assert transcript.index("Second policy\n  └ completed · 1.20s") < (
            transcript.index("First policy\n  └ failed · 32ms")
        )
    finally:
        await runtime.close()


@pytest.mark.anyio
@pytest.mark.parametrize(("status", "entry", "expected"), (
    (
        "completed",
        HookOutputEntry("warning", "Review the command"),
        "  └ completed · 25ms\n    warning: Review the command",
    ),
    (
        "blocked",
        HookOutputEntry("feedback", "Protected path"),
        "  └ blocked · 25ms\n    feedback: Protected path",
    ),
    (
        "stopped",
        HookOutputEntry("stop", "Do not continue"),
        "  └ stopped · 25ms\n    stop: Do not continue",
    ),
))
async def test_tui_hook_adapter_renders_result_semantics(
    status: str,
    entry: HookOutputEntry,
    expected: str,
) -> None:
    runtime = TuiRuntime()
    adapter = TuiHookStatusAdapter(runtime)

    try:
        await adapter.completed(_run(
            "result",
            status=status,
            duration_ms=25,
            entries=(entry,),
        ))

        assert expected in _block_text(runtime, 0)
    finally:
        await runtime.close()


@pytest.mark.anyio
async def test_tui_hook_records_follow_active_assistant_stream() -> None:
    runtime = TuiRuntime()
    adapter = TuiHookStatusAdapter(runtime)

    try:
        live = FragmentBlock((("", "assistant live"),))
        final = FragmentBlock((("", "assistant final"),))
        runtime.set_active_renderable(live, kind="assistant")

        await adapter.started(_run("during-stream"))

        assert fragments_text(runtime.document.active_block.fragments) == (
            "assistant live"
        )
        assert runtime.document.blocks == []

        runtime.commit_active_renderable(final)

        assert [item.kind for item in runtime.document.blocks] == [
            "assistant",
            "operation",
        ]
        assert _block_text(runtime, 0) == "assistant final"
        assert _block_text(runtime, 1) == "• Running PreToolUse hook"
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
        duration_ms=3500,
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

        assert display.startswith("• Ran PostToolUse hook\n  └ failed · 3.50s")
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
        duration_ms=0,
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

        assert display_line_count(display, width=width) == 5
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
        duration_ms=25,
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
async def test_tui_hook_records_are_committed_in_transcript() -> None:
    runtime = TuiRuntime()
    adapter = TuiHookStatusAdapter(runtime)

    try:
        await adapter.started(_run("record"))
        await adapter.completed(_run(
            "record",
            status="completed",
            duration_ms=25,
        ))
        runtime.toggle_transcript_overlay()

        transcript = fragments_text(runtime.screen.transcript_overlay.fragments())
        assert "Running PreToolUse hook" in transcript
        assert "PreToolUse hook\n  └ completed · 25ms" in transcript
        assert runtime.document.transcript_snapshot().live_tail is None
    finally:
        await runtime.close()


@pytest.mark.anyio
async def test_tui_hook_rows_reflow_after_resize() -> None:
    runtime = TuiRuntime()
    adapter = TuiHookStatusAdapter(runtime)
    runtime.screen._frame_geometry = FrameGeometry(20, 24, 1)
    completed = _run(
        "resize",
        status="failed",
        status_message="first status line\n" + "long status " * 8,
        entries=(HookOutputEntry("error", "long error " * 12),),
    )

    try:
        await adapter.completed(completed)
        transcript = fragments_text(
            runtime.document.blocks[0].transcript_block.fragments
        )
        narrow = fragments_text(runtime.document.fragments(
            width=20,
            reflow_sources=True,
        ))

        assert runtime.document.blocks[0].display_renderer is not None
        assert all(get_cwidth(line) <= 20 for line in narrow.splitlines())
        assert "\n  long status" in transcript

        runtime.document.set_display_width(60, reflow_sources=True)
        wide = fragments_text(runtime.document.fragments(
            width=60,
            reflow_sources=False,
        ))

        assert wide != narrow
        assert all(get_cwidth(line) <= 60 for line in wide.splitlines())
        assert fragments_text(
            runtime.document.blocks[0].transcript_block.fragments
        ) == transcript
    finally:
        await runtime.close()
