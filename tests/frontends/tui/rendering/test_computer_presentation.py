import asyncio

import pytest

from agent.application.views import (
    ApprovalView,
    NativeToolResultView,
    ToolStartView,
)
from agent.ports import (
    ApprovalPresentationChanged,
    OutputSurfaceContext,
    RecoveryChanged,
    ToolCompleted,
    ToolStarted,
    TurnTerminal,
)
from frontends.tui.adapters.session import create_tui_output_session
from frontends.tui.core.runtime import TuiRuntime
from frontends.terminal.renderers.computer import (
    computer_action,
    render_computer_activity,
)
from frontends.terminal.renderers.dispatch import render_presentation_transcript_view


def _context():
    return OutputSurfaceContext(surface_id="surface", cid="cid", sid="sid", turn_id="turn", agent_id="root")


def _scope():
    return {"surface_id": "surface", "turn_id": "turn"}


def _text(runtime):
    block = runtime.document.active_block
    return "" if block is None else "".join(text for _, text in block.fragments)


def _stable(runtime):
    return "\n".join("".join(text for _, text in block.display_block.fragments) for block in runtime.document.blocks)


def _result(call_id, action="observe", *, ok=True, error=""):
    return NativeToolResultView("computer", {"action": action, "screenshot": True}, ok,
                                {"error": error} if error else {"image": {"width": 100, "height": 60}, "tree": {"nodes": [{"name": "FULL RESULT"}]}},
                                12, call_id)


@pytest.mark.parametrize("width", [1, 3, 4, 20, 40, 80])
def test_computer_summary_respects_width_and_requires_screenshot_evidence(width):
    view = NativeToolResultView("computer", {"action": "observe", "screenshot": True}, True, {"image": None}, 10)
    display = render_computer_activity((computer_action(view),), finished=True, terminal_width=width)
    assert all(len(line) <= width for line in display.plain_text.splitlines())
    assert "Captured screenshot" not in display.plain_text


def test_computer_full_transcript_keeps_failure_explanation():
    view = NativeToolResultView("computer", {"action": "observe", "window": "window-1"}, False,
                                {"error": "stale_observation"}, 10, "one", "The window changed during capture.")
    transcript = render_presentation_transcript_view(view)[0].plain_text
    assert "The window changed during capture." in transcript
    assert "window-1" in transcript and "stale_observation" in transcript


async def _start(session, call_id, action="observe"):
    await session.activity.emit(ToolStarted(**_scope(), tool_id=call_id, tool_kind="client", name="computer"))
    await session.presentation.emit(ToolStartView("computer", {"action": action, "text": "PRIVATE INPUT"}, call_id))


async def _complete(session, call_id, action="observe", *, ok=True, error=""):
    await session.presentation.emit(_result(call_id, action, ok=ok, error=error))
    await session.activity.emit(ToolCompleted(**_scope(), tool_id=call_id, tool_kind="client", name="computer"))


@pytest.mark.anyio
async def test_computer_group_updates_one_tail_and_retains_full_transcript():
    runtime = TuiRuntime()
    session = create_tui_output_session("", context=_context(), runtime=runtime)
    await session.open()
    try:
        await _start(session, "one", "type_text")
        assert "Using computer · 1 action" in _text(runtime)
        assert "PRIVATE INPUT" not in _text(runtime)
        assert runtime.screen.activity_block is None
        before = _text(runtime)
        runtime.activity._render_slots()
        assert _text(runtime) == before
        await _complete(session, "one", "type_text")
        assert "Using computer" in _text(runtime)
        assert "Typed text" in _text(runtime)
        assert "Used computer" not in _text(runtime)
        await _start(session, "two")
        await _complete(session, "two")
        assert "2 actions" in _text(runtime)
        assert runtime.document.blocks == []
        assert "FULL RESULT" not in _text(runtime)
        transcript = "".join(text for _, text in runtime.document.active_transcript_block.fragments)
        assert "PRIVATE INPUT" in transcript and "FULL RESULT" in transcript
        await session.control.append_assistant_delta("Verified outcome\n")
        await session.control.settle_stream()
        assert len([block for block in runtime.document.blocks if block.kind == "operation"]) == 1
        assert "Used computer · 2 actions" in _stable(runtime)
        assert "Captured screenshot" in _stable(runtime)
        assert "Using computer" not in _stable(runtime)
    finally:
        await session.close()
        await runtime.activity.clear()


@pytest.mark.anyio
async def test_computer_approval_pauses_animation_and_stays_in_group():
    runtime = TuiRuntime()
    session = create_tui_output_session("", context=_context(), runtime=runtime)
    await session.open()
    try:
        await _start(session, "one")
        await session.activity.emit(ApprovalPresentationChanged(**_scope(), active=True))
        paused = _text(runtime)
        assert paused.startswith("• ")
        await asyncio.sleep(0.08)
        assert _text(runtime) == paused
        await session.presentation.emit(ApprovalView(
            approval={"tool": "computer", "call_id": "one"}, decision="accept", state="approved",
        ))
        await session.activity.emit(ApprovalPresentationChanged(**_scope(), active=False))
        assert not _text(runtime).startswith("• ")
        await _complete(session, "one", ok=False, error="stale_observation")
        await session.control.prepare_assistant_output()
        assert _stable(runtime).count("Used computer") == 1
        assert "1 failed" in _stable(runtime) and "stale_observation" in _stable(runtime)
        assert "1 action" in _stable(runtime)
    finally:
        await session.close()
        await runtime.activity.clear()


@pytest.mark.anyio
@pytest.mark.parametrize("status", ["interrupted", "cancelled", "failed"])
async def test_computer_terminal_stops_animation_and_does_not_invent_results(status):
    runtime = TuiRuntime()
    session = create_tui_output_session("", context=_context(), runtime=runtime)
    await session.open()
    try:
        await _start(session, "one")
        await _start(session, "two", "click")
        await _complete(session, "two", "click")
        assert "Inspecting window" in _text(runtime)
        await session.activity.emit(TurnTerminal(**_scope(), status=status))
        text = _stable(runtime)
        assert "Used computer · 2 actions" in text
        assert ("1 interrupted" if status != "failed" else "1 unconfirmed") in text
        assert "result unconfirmed" in text
        assert runtime.document.active_block is None
        assert runtime.screen.activity_block is None
        await asyncio.sleep(0.08)
        assert _stable(runtime) == text
        await session.presentation.emit(_result("one"))
        assert _stable(runtime) == text and runtime.document.active_block is None
    finally:
        await session.close()
        await runtime.activity.clear()


@pytest.mark.anyio
async def test_computer_replay_deduplicates_results_and_freezes_animation():
    displays = []
    for replay in (False, True):
        runtime = TuiRuntime()
        session = create_tui_output_session("", context=_context(), runtime=runtime)
        await session.open()
        try:
            if replay:
                await session.activity.emit(RecoveryChanged(**_scope(), mode="replaying", event_seq=1))
            for call_id in ("one", "two"):
                if not replay:
                    await _start(session, call_id)
                if replay:
                    await session.presentation.emit(_result(call_id))
                else:
                    await _complete(session, call_id)
                await session.presentation.emit(_result(call_id))
                await session.presentation.emit(ToolStartView("computer", {"action": "observe"}, call_id))
            if replay:
                assert _text(runtime).startswith("• ")
            await session.close()
            displays.append(_stable(runtime))
            assert "Used computer · 2 actions" in displays[-1]
        finally:
            await session.close()
            await runtime.activity.clear()
    assert displays[0] == displays[1]


@pytest.mark.anyio
async def test_computer_other_tool_boundary_and_resize_preserve_details():
    runtime = TuiRuntime()
    session = create_tui_output_session("", context=_context(), runtime=runtime, animate=False)
    await session.open()
    try:
        await _start(session, "one")
        assert _text(runtime).startswith("• ")
        await _complete(session, "one")
        await session.presentation.emit(ToolStartView("js_repl", {"code": "console.log('next')"}, "other"))
        await _start(session, "two", "click")
        await _complete(session, "two", "click")
        await session.close()
        assert _stable(runtime).count("Used computer · 1 action") == 2
        for block in runtime.document.blocks:
            if block.display_renderer:
                narrow = "".join(text for _, text in block.display_renderer(30).fragments)
                assert all(len(line) <= 30 for line in narrow.splitlines())
    finally:
        await session.close()
        await runtime.activity.clear()
