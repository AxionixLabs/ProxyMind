# -*- coding: utf-8 -*-

import io
import json
from unittest.mock import (
    AsyncMock,
    Mock,
)

import pytest

from agent.application.turns.lifecycle import handle_lifecycle_event
from agent.application.views import ContextCompactionView
from frontends.output.jsonl import (
    JsonOutputState,
    JsonPresentationSink,
)
from frontends.terminal.renderers.dispatch import render_presentation_view
from frontends.tui.adapters.output import TuiOutputControl
from frontends.tui.adapters.presentation import TuiPresentationSink
from frontends.tui.core.render import fragments_text
from frontends.tui.core.runtime import TuiRuntime
from protocol.schema.stream_events import parse_stream_event
from agent.application.views.builders.tools import build_native_tool_result_view


class _RecordWriter:
    """提供 JSONL 展示测试需要的最小记录器。"""

    def write_raw(self, text: str) -> None:
        _ = text

    def flush(self) -> None:
        return None


def _view(
    status: str = "completed",
    *,
    trigger: str = "automatic",
    phase: str = "mid_turn",
) -> ContextCompactionView:
    return ContextCompactionView(
        turn_id="turn_test",
        item_id="compaction_test",
        event_seq=3,
        presentation_epoch=1,
        status=status,
        phase=phase,
        trigger=trigger,
        reason="context_limit",
        error_type="summary_failed" if status == "failed" else None,
        retryable=True if status == "failed" else None,
        before_items=18 if status == "completed" else None,
        after_items=7 if status == "completed" else None,
        replacement_version=4 if status == "completed" else None,
    )


@pytest.mark.anyio
async def test_compaction_event_uses_shared_lifecycle_projection() -> None:
    presentation = AsyncMock()
    transcript = Mock()
    event = parse_stream_event({
        "type": "context.compaction.completed",
        "proto": "mind.chat",
        "cid": "cid_test",
        "sid": "sid_test",
        "turn_id": "turn_test",
        "event_seq": 3,
        "presentation_epoch": 1,
        "item_id": "compaction_test",
        "item_kind": "context_compaction",
        "item_status": "completed",
        "phase": "mid_turn",
        "trigger": "automatic",
        "reason": "context_limit",
        "before_items": 18,
        "after_items": 7,
        "replacement_version": 4,
    })

    assert await handle_lifecycle_event(
        event,
        presentation=presentation,
        transcript=transcript,
    ) is True
    projected = presentation.emit.await_args.args[0]
    assert isinstance(projected, ContextCompactionView)
    assert projected.turn_id == "turn_test"
    assert projected.item_id == "compaction_test"
    assert projected.status == "completed"
    assert projected.replacement_version == 4
    transcript.append.assert_called_once()
    transcript_payload = transcript.append.call_args.kwargs["payload"]
    assert transcript_payload["item_id"] == "compaction_test"
    assert transcript_payload["event_seq"] == 3
    assert "summary" not in transcript_payload


def test_only_completed_compaction_renders_permanent_notice() -> None:
    assert render_presentation_view(_view("in_progress")) == ()
    assert render_presentation_view(_view("failed")) == ()
    assert render_presentation_view(_view())[0].plain_text == (
        "• Context compacted"
    )


@pytest.mark.anyio
async def test_jsonl_preserves_compaction_lifecycle_without_summary() -> None:
    stdout = io.StringIO()
    state = JsonOutputState(_RecordWriter(), stdout)
    presentation = JsonPresentationSink(state)

    await presentation.emit(_view("in_progress"))
    await presentation.emit(_view())

    events = [json.loads(line) for line in stdout.getvalue().splitlines()]
    assert [event["type"] for event in events] == [
        "item.started",
        "item.completed",
    ]
    assert events[0]["item"]["id"] == events[1]["item"]["id"]
    assert events[1]["item"]["type"] == "context_compaction"
    assert events[1]["item"]["replacement_version"] == 4
    assert "summary" not in events[1]["item"]


@pytest.mark.anyio
async def test_tui_inserts_separator_after_work_and_compaction() -> None:
    runtime = TuiRuntime()
    output = TuiOutputControl("", runtime=runtime, animate=False)
    presentation = TuiPresentationSink(output)

    await presentation.emit(build_native_tool_result_view(
        "shell_command",
        {"command": "echo done"},
        ok=True,
        data={"command": "echo done", "output_lines": ["done"]},
        call_id="done",
    ))
    await presentation.emit(_view())
    await output.append_assistant_delta("continued")
    await output.prepare_external_output()

    assert [item.kind for item in runtime.document.blocks] == [
        "operation",
        "notice",
        "system",
        "assistant",
    ]
    rendered = fragments_text(runtime.document.fragments(width=40))
    assert "Context compacted" in rendered
    assert "─" * 40 in rendered
    assert rendered.index("Context compacted") < rendered.index("─" * 40)


@pytest.mark.anyio
async def test_tui_compaction_without_work_does_not_insert_separator() -> None:
    runtime = TuiRuntime()
    output = TuiOutputControl("", runtime=runtime, animate=False)
    presentation = TuiPresentationSink(output)

    await presentation.emit(_view())
    await output.append_assistant_delta("continued")
    await output.prepare_external_output()

    assert [item.kind for item in runtime.document.blocks] == [
        "notice",
        "assistant",
    ]
    assert "─" * 40 not in fragments_text(
        runtime.document.fragments(width=40)
    )


@pytest.mark.anyio
async def test_failed_or_manual_compaction_does_not_arm_tui_boundary() -> None:
    for view in (_view("failed"), _view(trigger="manual", phase="standalone")):
        runtime = TuiRuntime()
        output = TuiOutputControl("", runtime=runtime, animate=False)
        presentation = TuiPresentationSink(output)

        await presentation.emit(view)

        assert output._needs_final_message_separator is False
