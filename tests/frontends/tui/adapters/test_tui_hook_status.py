# -*- coding: utf-8 -*-

import asyncio
import time
from dataclasses import replace

import pytest
from prompt_toolkit.utils import get_cwidth

from agent.application.hooks.models import (
    HookOutputEntry,
    HookRunSummary,
)
from frontends.tui.adapters.hooks import TuiHookStatusAdapter
from frontends.tui.core.models import FragmentBlock
from frontends.tui.core.render import fragments_text
from frontends.tui.core.runtime import TuiRuntime
from frontends.tui.core.screen import FrameGeometry


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
        started_at=time.monotonic(),
        duration_ms=duration_ms,
        entries=entries,
    )


def _block_text(runtime: TuiRuntime, index: int) -> str:
    return fragments_text(runtime.document.blocks[index].display_block.fragments)


@pytest.mark.anyio
@pytest.mark.parametrize(("elapsed", "label"), [(0, "0s"), (65, "1m 05s")])
async def test_tui_hook_adapter_delays_activity_with_its_own_elapsed_time(elapsed, label) -> None:
    runtime = TuiRuntime()
    adapter = TuiHookStatusAdapter(runtime)

    try:
        await adapter.started(replace(_run(
            "started",
            event="UserPromptSubmit",
            status_message="Checking prompt",
        ), started_at=time.monotonic() - elapsed))

        assert runtime.screen.activity_block is None
        assert runtime.document.blocks == []
        await asyncio.sleep(0.4)
        assert fragments_text(runtime.screen.activity_block.fragments) == f"• Checking prompt · {label}"
        assert runtime.document.blocks == []
    finally:
        await runtime.close()


@pytest.mark.anyio
async def test_tui_hook_adapter_shares_and_restores_thinking_activity() -> None:
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
        await asyncio.sleep(0.4)
        assert fragments_text(runtime.screen.activity_block.fragments) == "• Checking command · 0s"
        await adapter.completed(_run("started", status="completed"))
        assert "Thinking" in fragments_text(runtime.screen.activity_block.fragments)
        assert runtime.document.blocks == []
    finally:
        await runtime.close()


@pytest.mark.anyio
@pytest.mark.parametrize("entries", [(), (HookOutputEntry("context", "Model-only instructions"),)])
async def test_tui_hook_adapter_omits_quiet_success_result(entries) -> None:
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
            entries=entries,
        ))

        assert runtime.document.blocks == []
        assert runtime.screen.activity_block is None
    finally:
        await runtime.close()


@pytest.mark.anyio
async def test_tui_hook_adapter_combines_concurrent_activity_and_keeps_failure() -> None:
    runtime = TuiRuntime()
    adapter = TuiHookStatusAdapter(runtime)

    try:
        await adapter.started(_run("first", status_message="First policy"))
        await adapter.started(_run("second", status_message="Second policy"))
        await asyncio.sleep(0.4)
        assert fragments_text(runtime.screen.activity_block.fragments) == "• Running hooks · 0s"
        await adapter.completed(_run(
            "second",
            status="completed",
            status_message="Second policy",
            duration_ms=1200,
        ))
        assert fragments_text(runtime.screen.activity_block.fragments) == "• First policy · 0s"
        await adapter.completed(_run(
            "first",
            status="failed",
            status_message="First policy",
            duration_ms=32,
            entries=(HookOutputEntry("error", "First policy failed"),),
        ))

        assert len(runtime.document.blocks) == 1
        assert runtime.screen.activity_block is None
        assert _block_text(runtime, 0) == (
            "• First policy · failed · 32ms\n"
            "  └ error: First policy failed"
        )

        runtime.toggle_transcript_overlay()
        transcript = fragments_text(
            runtime.screen.transcript_overlay.fragments()
        )
        assert "Second policy" not in transcript
        assert "First policy failed" in transcript
    finally:
        await runtime.close()


@pytest.mark.anyio
@pytest.mark.parametrize(("status", "entry", "expected"), (
    (
        "completed",
        HookOutputEntry("warning", "Review the command"),
        " · completed · 25ms\n  └ warning: Review the command",
    ),
    (
        "blocked",
        HookOutputEntry("feedback", "Protected path"),
        " · blocked · 25ms\n  └ feedback: Protected path",
    ),
    (
        "stopped",
        HookOutputEntry("stop", "Do not continue"),
        " · stopped · 25ms\n  └ stop: Do not continue",
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
        await adapter.completed(_run(
            "during-stream", status="completed",
            entries=(HookOutputEntry("warning", "Review output"),),
        ))

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
        assert "Review output" in _block_text(runtime, 1)
    finally:
        await runtime.close()


@pytest.mark.anyio
async def test_tui_hook_adapter_persists_failure_without_model_context() -> None:
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

        assert display.startswith("• PostToolUse hook · failed · 3.50s")
        assert "context line" not in display
        assert "context line" not in transcript
        assert "error: review failed" in transcript
        assert block.display_block.fragments[0][0] == "class:terminal.failure bold"
        assert block.raw_text == transcript
    finally:
        await runtime.close()


@pytest.mark.anyio
@pytest.mark.parametrize(("indicator", "background"), [
    ("thinking", True), ("tool", True), ("retrying", True), ("retrying", False),
])
async def test_hook_details_preserve_foreground_and_recovery(indicator, background) -> None:
    runtime = TuiRuntime()
    adapter = TuiHookStatusAdapter(runtime)
    try:
        await runtime.activity.show_turn_surface(title="Foreground", indicator=indicator)
        await adapter.started(replace(
            _run("slow", status_message="Checking command"),
            execution_mode="async" if background else "sync",
        ))
        await asyncio.sleep(0.4)
        activity = fragments_text(runtime.screen.activity_block.fragments)
        assert activity.splitlines()[0].startswith("• Foreground")
        assert "Checking command · 0s" in activity.splitlines()[1]
        assert len(activity.splitlines()) == 2
        assert runtime.document.blocks == []
        await adapter.completed(_run("slow", status="completed"))
        assert "Foreground" in fragments_text(runtime.screen.activity_block.fragments)
        assert "Checking command" not in fragments_text(runtime.screen.activity_block.fragments)
    finally:
        await runtime.close()


@pytest.mark.anyio
async def test_synchronous_hook_keeps_its_label_and_clock_beside_background_hook() -> None:
    runtime = TuiRuntime()
    adapter = TuiHookStatusAdapter(runtime)
    try:
        await runtime.begin_wait_status()
        await adapter.started(replace(
            _run("background", status_message="Auditing request"),
            execution_mode="async", started_at=time.monotonic() - 65,
        ))
        await adapter.started(_run("foreground", status_message="Checking command"))
        await asyncio.sleep(0.4)
        lines = fragments_text(runtime.screen.activity_block.fragments).splitlines()
        assert lines == [
            "• Checking command · 0s",
            "  └ Background hook: Auditing request · 1m 05s",
        ]
        await adapter.completed(_run("foreground", status="completed"))
        activity = fragments_text(runtime.screen.activity_block.fragments)
        assert "Thinking" in activity
        assert "Auditing request · 1m 05s" in activity
    finally:
        await runtime.close()


@pytest.mark.anyio
async def test_hook_elapsed_survives_narrow_width_and_approval_pause() -> None:
    runtime = TuiRuntime()
    adapter = TuiHookStatusAdapter(runtime)
    runtime.screen._frame_geometry = FrameGeometry(32, 24, 1)

    try:
        await runtime.begin_wait_status()
        await adapter.started(replace(
            _run("slow", status_message="Checking 中文 " * 10),
            started_at=time.monotonic() - 65,
        ))
        await asyncio.sleep(0.4)
        display = fragments_text(runtime.screen.activity_block.fragments)
        assert get_cwidth(display) <= 32
        assert display.endswith(" · 1m 05s")
        assert runtime.activity.pause_wait()
        assert runtime.screen.activity_block is None
        await asyncio.sleep(0.1)
        assert runtime.screen.activity_block is None
        await runtime.activity.ensure_wait()
        assert "1m 05s" in fragments_text(runtime.screen.activity_block.fragments)
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
            entries=(HookOutputEntry("warning", "Hook warning"),),
        ))
        runtime.toggle_transcript_overlay()

        transcript = fragments_text(runtime.screen.transcript_overlay.fragments())
        assert "PreToolUse hook · completed · 25ms" in transcript
        assert "Hook warning" in transcript
        assert runtime.document.transcript_snapshot().live_tail is None
    finally:
        await runtime.close()


@pytest.mark.anyio
@pytest.mark.parametrize("scope", ["turn", "thread"])
async def test_interrupted_hook_cannot_reappear_in_next_turn(scope) -> None:
    runtime = TuiRuntime()
    adapter = TuiHookStatusAdapter(runtime)
    try:
        runtime.set_execution_active(True)
        await adapter.started(replace(_run("old", status_message="Old hook"), scope=scope))
        await asyncio.sleep(0.4)
        runtime.prepare_interrupted_presentation()
        assert runtime.screen.activity_block is None
        runtime.set_execution_active(False)
        runtime.set_execution_active(True)
        await runtime.begin_wait_status()
        await adapter.completed(replace(_run(
            "old", status="failed", entries=(HookOutputEntry("error", "Old failure"),),
        ), scope=scope))
        assert runtime.document.blocks == []
        assert "Thinking" in fragments_text(runtime.screen.activity_block.fragments)
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
        assert "first status line long status" in transcript

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
