import asyncio
from unittest.mock import patch

import pytest

from agent.application.tools.execution import ToolExecutionResult
from agent.application.views import (
    ApprovalView,
    ToolStartView,
)
from agent.application.views.builders.tools import build_generic_tool_result_view
from agent.application.views.tool_execution import show_tool_result
from agent.domain.transcripts import (
    TranscriptEntry,
    TranscriptReplay,
)
from agent.ports import (
    ApprovalPresentationChanged,
    ModelWaitRequested,
    OutputSurfaceContext,
    ToolCompleted,
    ToolStarted,
    TurnTerminal,
)
from frontends.tui.adapters.session import create_tui_output_session
from frontends.tui.core.runtime import TuiRuntime
from frontends.tui.core.queued import TuiSubmission
from frontends.tui.features.history import _render_replay_blocks


CONTEXT = OutputSurfaceContext(surface_id="surface", cid="cid", sid="sid", turn_id="turn", agent_id="root")
SCOPE = {"surface_id": "surface", "turn_id": "turn"}


def _text(block):
    return "" if block is None else "".join(text for _, text in block.fragments)


async def _start(session, call_id):
    await session.activity.emit(ToolStarted(**SCOPE, tool_id=call_id, tool_kind="client", name="remote_tool"))
    await session.presentation.emit(ToolStartView("remote_tool", {"query": call_id}, call_id))


@pytest.mark.anyio
@pytest.mark.parametrize("status", ["completed", "failed", "declined", "cancelled"])
async def test_empty_result_settles_same_call_with_actual_status(status):
    runtime = TuiRuntime()
    session = create_tui_output_session("", context=CONTEXT, runtime=runtime)
    await session.open()
    try:
        await _start(session, "one")
        assert "Calling remote_tool" in _text(runtime.document.active_block)
        assert runtime.screen.activity_block is None
        result = ToolExecutionResult(status == "completed", {}, "", {}, None, 0, status)
        await show_tool_result(session.presentation, "remote_tool", {"query": "one"}, result, call_id="one")
        assert len(runtime.document.blocks) == 1
        assert runtime.document.active_block is None
        block = runtime.document.blocks[0]
        assert "Called remote_tool" in _text(block.display_block)
        assert ("no text output" if status == "completed" else status) in _text(block.display_block)
        assert '"query": "one"' in _text(block.transcript_block)
        assert runtime.screen.activity_block is None
        await session.activity.emit_batch((
            ToolCompleted(**SCOPE, tool_id="one", tool_kind="client", name="remote_tool"),
            ModelWaitRequested(**SCOPE, revision=1, reason="tool_result"),
        ))
        await asyncio.sleep(0.2)
        assert "Thinking" in _text(runtime.screen.activity_block)
        assert "Calling" not in _text(block.display_block)
    finally:
        await session.close()
        await runtime.activity.clear()


@pytest.mark.anyio
async def test_same_name_calls_and_interleaved_body_keep_identity_order_and_details():
    runtime = TuiRuntime()
    session = create_tui_output_session("", context=CONTEXT, runtime=runtime, animate=False)
    await session.open()
    try:
        await _start(session, "one")
        await _start(session, "two")
        await session.control.append_assistant_delta("Interleaved body\n")
        for call_id in ("two", "one"):
            result = build_generic_tool_result_view("remote_tool", f"result {call_id}", ok=True, call_id=call_id)
            await session.presentation.emit(result)
            await session.presentation.emit(result)
            await session.presentation.emit(ToolStartView("remote_tool", {}, call_id))
        await session.control.prepare_external_output()
        calls = [block for block in runtime.document.blocks if block.kind == "operation"]
        assert len(calls) == 2
        assert "result one" in _text(calls[0].display_block)
        assert "result two" in _text(calls[1].display_block)
        for block, call_id in zip(calls, ("one", "two"), strict=True):
            assert f'"query": "{call_id}"' in _text(block.transcript_block)
            assert f"result {call_id}" in block.raw_text
        assert "Interleaved body" in _text(runtime.document.blocks[-1].display_block)
    finally:
        await session.close()
        await runtime.activity.clear()


@pytest.mark.anyio
async def test_approval_pauses_and_resumes_one_call_without_reserializing_each_frame():
    runtime = TuiRuntime()
    session = create_tui_output_session("", context=CONTEXT, runtime=runtime)
    await session.open()
    try:
        await _start(session, "one")
        with patch("frontends.tui.adapters.tool_calls.call_transcript", side_effect=AssertionError("frame serialized payload")):
            runtime.activity._render_slots()
            before = runtime.document.active_block
            cycle = runtime.activity._tool_breath.cycle
            await session.activity.emit(ApprovalPresentationChanged(**SCOPE, active=True))
            paused = _text(runtime.document.active_block)
            assert paused.startswith("• Calling")
            await asyncio.sleep(0.08)
            assert runtime.document.active_block == before
            assert runtime.activity._tool_breath.cycle == cycle
            await session.presentation.emit(ApprovalView(
                approval={"tool": "remote_tool", "call_id": "one"}, decision="accept", state="approved",
            ))
            await session.activity.emit(ApprovalPresentationChanged(**SCOPE, active=False))
            assert _text(runtime.document.active_block) == paused
            assert runtime.activity.active
            assert runtime.screen.activity_block is None
    finally:
        await session.close()
        await runtime.activity.clear()


@pytest.mark.anyio
@pytest.mark.parametrize("before_start", [False, True])
async def test_terminal_freezes_pending_call_and_rejects_late_presentation(before_start):
    runtime = TuiRuntime()
    session = create_tui_output_session("", context=CONTEXT, runtime=runtime)
    await session.open()
    try:
        if not before_start:
            await _start(session, "one")
        await session.activity.emit(TurnTerminal(**SCOPE, status="interrupted"))
        before = tuple(runtime.document.blocks)
        if not before_start:
            assert "Interrupted remote_tool" in _text(before[0].display_block)
            assert "Result unconfirmed" in _text(before[0].display_block)
        await session.presentation.emit(ToolStartView("remote_tool", {}, "late"))
        await session.presentation.emit(build_generic_tool_result_view("remote_tool", "late result", ok=True, call_id="one"))
        assert tuple(runtime.document.blocks) == before
        assert runtime.document.active_block is None
        assert runtime.screen.activity_block is None
    finally:
        await session.close()
        await runtime.activity.clear()


@pytest.mark.anyio
async def test_replayed_call_matches_live_display_and_preserves_arguments():
    runtime = TuiRuntime()
    session = create_tui_output_session("", context=CONTEXT, runtime=runtime, animate=False)
    await session.open()
    try:
        await _start(session, "one")
        await session.presentation.emit(build_generic_tool_result_view("remote_tool", "result", ok=True, call_id="one"))
        entries = tuple(TranscriptEntry(
            timestamp="2026-11-06T00:00:00Z", event=event, session_id="sid", turn_id="turn", actor="tool", payload=payload,
        ) for event, payload in (
            ("tool.started", {"call_id": "one", "name": "remote_tool", "arguments": {"query": "one"}}),
            ("tool.completed", {"call_id": "one", "ok": True, "result": {"ok": True, "text": "result", "attachments": [], "data": {}}}),
        ))
        replay = _render_replay_blocks(TranscriptReplay(entries).build(), terminal_width=session.control.terminal_width)
        assert len(replay) == 1
        assert _text(replay[0].display_block) == _text(runtime.document.blocks[0].display_block)
        assert _text(replay[0].transcript_block) == _text(runtime.document.blocks[0].transcript_block)
    finally:
        await session.close()
        await runtime.activity.clear()


@pytest.mark.anyio
@pytest.mark.parametrize("completed", [False, True])
@pytest.mark.parametrize("name,title", [("remote_tool", "Calling remote_tool"), ("next_tool", "Calling next_tool"), ("computer", "Using computer"), ("shell_command", "Thinking")])
async def test_new_tool_activity_cannot_be_consumed_by_previous_call(completed, name, title):
    runtime = TuiRuntime()
    session = create_tui_output_session("", context=CONTEXT, runtime=runtime)
    await session.open()
    try:
        await _start(session, "one")
        if completed:
            await session.presentation.emit(build_generic_tool_result_view("remote_tool", "done", ok=True, call_id="one"))
        await session.activity.emit(ToolStarted(**SCOPE, tool_id="two", tool_kind="client", name=name))
        assert title in _text(runtime.screen.activity_block)
        if not completed:
            assert _text(runtime.document.active_block).startswith("• Calling remote_tool")
    finally:
        await session.close()
        await runtime.activity.clear()


@pytest.mark.anyio
@pytest.mark.parametrize("name", ["remote_tool", "computer"])
async def test_steer_submission_settles_tools_once_before_next_query(name):
    runtime = TuiRuntime()
    runtime.set_execution_active(True)
    session = create_tui_output_session("", context=CONTEXT, runtime=runtime)
    await session.open()
    try:
        await session.activity.emit(ToolStarted(**SCOPE, tool_id="one", tool_kind="client", name=name))
        await session.presentation.emit(ToolStartView(name, {"action": "observe"}, "one"))
        runtime._present_interrupt_submission(TuiSubmission(value="NEXT QUERY", editable_text="NEXT QUERY", paste_store={}))
        lease = runtime.activity.lease("wait")
        await session.activity.emit(TurnTerminal(**SCOPE, status="interrupted"))
        await session.close()
        assert runtime.activity.lease("wait") == lease
        assert len(runtime.document.blocks) == 2
        assert runtime.document.blocks[-1].raw_text == "NEXT QUERY"
        previous = _text(runtime.document.blocks[0].display_block)
        assert "unconfirmed" in previous.lower()
        assert "Calling" not in previous and "Using computer" not in previous
        assert runtime.document.active_block is None
    finally:
        await session.close()
        await runtime.activity.clear()


@pytest.mark.anyio
@pytest.mark.parametrize("name,title", [("remote_tool", "Calling"), ("computer", "Using computer")])
async def test_interrupted_tool_does_not_reopen_fallback_while_waiting_for_terminal(name, title):
    runtime = TuiRuntime()
    runtime.set_execution_active(True)
    session = create_tui_output_session("", context=CONTEXT, runtime=runtime)
    await session.open()
    try:
        await session.activity.emit(ToolStarted(**SCOPE, tool_id="one", tool_kind="client", name=name))
        await session.presentation.emit(ToolStartView(name, {"action": "observe"}, "one"))
        lease = runtime.activity.lease("wait")
        runtime.begin_interrupt_settlement()
        runtime.prepare_interrupted_presentation()
        runtime.activity._render_slots()
        assert runtime.document.active_block is None
        assert title not in _text(runtime.screen.activity_block)
        assert runtime.activity.lease("wait") == lease
        assert runtime.execution_active
    finally:
        await session.close()
        runtime.set_execution_active(False)
        await runtime.activity.clear()


@pytest.mark.anyio
@pytest.mark.parametrize("name,title", [("remote_tool", "Calling"), ("computer", "Using computer")])
async def test_tool_title_style_is_stable_before_and_after_presentation(name, title):
    runtime = TuiRuntime()
    session = create_tui_output_session("", context=CONTEXT, runtime=runtime)
    await session.open()
    try:
        await session.activity.emit(ToolStarted(**SCOPE, tool_id="one", tool_kind="client", name=name))
        status = runtime.screen.activity_block
        assert status is not None
        before = next(style for style, text in status.fragments if title in text)
        await session.presentation.emit(ToolStartView(name, {"action": "observe"}, "one"))
        active = runtime.document.active_block
        assert active is not None
        after = next(style for style, text in active.fragments if title in text)
        assert before == after
        renderer = runtime.document.active_display_renderer
        assert renderer is not None
        for width in (40, 120):
            assert renderer(width).fragments[0] == active.fragments[0]
    finally:
        await session.close()
        await runtime.activity.clear()
