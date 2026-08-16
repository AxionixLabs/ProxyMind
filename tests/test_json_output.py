# -*- coding: utf-8 -*-

import io
import json

import pytest

from mind_app.output.content import (
    AssistantResponseSuperseded,
    AssistantSegmentCompleted,
    AssistantTextDelta,
)
from mind_app.output.jsonl import (
    JsonContentSink,
    JsonPresentationSink,
    JsonOutputState,
)
from mind_app.presentation.models import (
    ApprovalView,
    FailureView,
    HookOutputView,
    HookRunView,
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
async def test_json_output_distinguishes_response_retry_from_worker_takeover() -> None:
    """验证结构化输出保留 provider response 的精确替换作用域。"""
    stdout = io.StringIO()
    state = JsonOutputState(_RecordWriter(), stdout)
    content = JsonContentSink(state)

    await content.emit(AssistantResponseSuperseded(
        presentation_epoch=1,
        round=2,
        attempt=2,
    ))

    assert json.loads(stdout.getvalue()) == {
        "type": "response.superseded",
        "presentation_epoch": 1,
        "round": 2,
        "attempt": 2,
    }


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


@pytest.mark.anyio
async def test_json_output_emits_structured_hook_and_approval_items() -> None:
    stdout = io.StringIO()
    state = JsonOutputState(_RecordWriter(), stdout)
    presentation = JsonPresentationSink(state)

    await presentation.emit(HookRunView(
        id="hook-1",
        hook_key="project:prompt",
        event="UserPromptSubmit",
        phase="started",
        status="running",
        status_message="Checking prompt",
    ))
    await presentation.emit(HookRunView(
        id="hook-1",
        hook_key="project:prompt",
        event="UserPromptSubmit",
        phase="completed",
        status="completed",
        status_message="Checking prompt",
        duration_ms=25,
        entries=(HookOutputView("context", "safe context"),),
    ))
    await presentation.emit(ApprovalView(
        approval={
            "id": "approval-1",
            "tool": "shell_command",
            "command": "pytest -q",
        },
        decision="decline",
        state="denied",
        source="policy",
    ))

    events = [json.loads(line) for line in stdout.getvalue().splitlines()]
    assert events == [
        {
            "type": "item.started",
            "item": {
                "id": "hook-1",
                "type": "hook",
                "hook_key": "project:prompt",
                "event": "UserPromptSubmit",
                "status": "in_progress",
                "status_message": "Checking prompt",
            },
        },
        {
            "type": "item.completed",
            "item": {
                "id": "hook-1",
                "type": "hook",
                "hook_key": "project:prompt",
                "event": "UserPromptSubmit",
                "status": "completed",
                "status_message": "Checking prompt",
                "duration_ms": 25,
                "entries": [{"kind": "context", "text": "safe context"}],
            },
        },
        {
            "type": "item.completed",
            "item": {
                "id": "approval-1",
                "type": "approval",
                "tool": "shell_command",
                "approval": {
                    "id": "approval-1",
                    "tool": "shell_command",
                    "command": "pytest -q",
                },
                "decision": "decline",
                "status": "denied",
                "source": "policy",
            },
        },
    ]


@pytest.mark.anyio
async def test_json_hook_item_id_uses_shared_collision_registry() -> None:
    stdout = io.StringIO()
    state = JsonOutputState(_RecordWriter(), stdout)
    presentation = JsonPresentationSink(state)

    assert state.item_id() == "item_0"

    await presentation.emit(HookRunView(
        id="item_0",
        hook_key="project:prompt",
        event="UserPromptSubmit",
        phase="started",
        status="running",
    ))
    await presentation.emit(HookRunView(
        id="item_0",
        hook_key="project:prompt",
        event="UserPromptSubmit",
        phase="completed",
        status="completed",
        duration_ms=1,
    ))

    events = [json.loads(line) for line in stdout.getvalue().splitlines()]
    assert [event["item"]["id"] for event in events] == ["item_1", "item_1"]
