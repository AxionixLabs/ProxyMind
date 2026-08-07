# -*- coding: utf-8 -*-

import io
import json

import pytest

from mind_app.output.content import (
    AssistantSegmentCompleted,
    AssistantTextDelta,
)
from mind_app.output.jsonl import (
    JsonContentSink,
    JsonPresentationSink,
    JsonOutputState,
)
from mind_app.presentation.models import (
    FailureView,
    RunCompletedView,
    RunIncompleteView,
)


class _RecordWriter(object):
    async def open(self) -> None:
        return None

    async def close(self) -> None:
        return None

    def write_raw(self, text: str) -> None:
        _ = text

    def flush(self) -> None:
        return None


@pytest.mark.anyio
async def test_json_output_initializes_and_flushes_assistant_state() -> None:
    stdout = io.StringIO()
    state = JsonOutputState(_RecordWriter(), stdout)
    content = JsonContentSink(state)

    await content.emit(AssistantTextDelta("first "))
    await content.emit(AssistantTextDelta("second"))

    assert stdout.getvalue() == ""
    await content.emit(AssistantSegmentCompleted())

    event = json.loads(stdout.getvalue())
    assert event["item"]["text"] == "first second"


@pytest.mark.anyio
async def test_json_output_preserves_structured_text_semantics() -> None:
    stdout = io.StringIO()
    state = JsonOutputState(_RecordWriter(), stdout)
    content = JsonContentSink(state)
    raw = "id\tdevice\x1b]52;c;payload\x1b\\"

    await content.emit(AssistantTextDelta(raw))
    await content.emit(AssistantSegmentCompleted())

    event = json.loads(stdout.getvalue())
    assert event["item"]["text"] == raw


@pytest.mark.anyio
async def test_json_output_emits_terminal_status_and_metadata() -> None:
    stdout = io.StringIO()
    state = JsonOutputState(_RecordWriter(), stdout)
    presentation = JsonPresentationSink(state)

    await presentation.emit(RunCompletedView(
        usage={"output_tokens": 3},
        response_id="msg_completed",
        model="claude-test",
        route="messages",
        request_id="req_completed",
        service_tier="standard",
        stop_reason="end_turn",
    ))
    await presentation.emit(RunIncompleteView(
        usage={"output_tokens": 7},
        reason="max_output_tokens",
        can_continue=False,
        response_id="msg_incomplete",
        route="messages",
        stop_reason="max_tokens",
    ))
    await presentation.emit(FailureView(
        phase="turn.failed",
        error="pause_turn is not supported",
        usage={"output_tokens": 2},
        route="messages",
        stop_reason="pause_turn",
    ))

    events = [json.loads(line) for line in stdout.getvalue().splitlines()]

    assert events == [
        {
            "type": "turn.completed",
            "status": "completed",
            "usage": {"output_tokens": 3},
            "response_id": "msg_completed",
            "model": "claude-test",
            "route": "messages",
            "request_id": "req_completed",
            "service_tier": "standard",
            "stop_reason": "end_turn",
        },
        {
            "type": "turn.incomplete",
            "status": "incomplete",
            "usage": {"output_tokens": 7},
            "response_id": "msg_incomplete",
            "route": "messages",
            "stop_reason": "max_tokens",
            "reason": "max_output_tokens",
            "can_continue": False,
        },
        {
            "type": "turn.failed",
            "error": "pause_turn is not supported",
            "phase": "turn.failed",
            "status": "failed",
            "usage": {"output_tokens": 2},
            "route": "messages",
            "stop_reason": "pause_turn",
        },
    ]
