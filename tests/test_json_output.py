# -*- coding: utf-8 -*-

import io
import json

import pytest

from mind_app.presentation.output.content import (
    AssistantPresentationSuperseded,
    AssistantResponseSuperseded,
    AssistantSegmentCompleted,
    AssistantTextDelta,
    ResponseIdentity,
)
from mind_app.presentation.output.jsonl import (
    JsonContentSink,
    JsonPresentationSink,
    JsonOutputControl,
    JsonOutputState,
)
from mind_app.presentation.models import (
    ApprovalView,
    FailureView,
    HookOutputView,
    HookRunView,
    RunCompletedView,
    RunIncompleteView,
    RunStartedView,
)
from mind_app.presentation.tool_views import build_native_tool_result_view


class _RecordWriter(object):
    async def open(self) -> None:
        return None

    async def close(self) -> None:
        return None

    def write_raw(self, text: str) -> None:
        _ = text

    def flush(self) -> None:
        return None


def _run_started_view(*, hook_warnings=()) -> RunStartedView:
    return RunStartedView(
        thread_id="thread_test",
        turn_id="turn_test",
        session_id="session_test",
        message="inspect",
        model="test-model",
        provider="test-provider",
        approval="never",
        workdir="D:/workspace",
        sandbox="read-only",
        reasoning_effort="none",
        reasoning_summaries="none",
        hook_warnings=hook_warnings,
    )


def _identity(
    *,
    turn_id: str = "turn_test",
    presentation_epoch: int = 1,
    round_no: int = 1,
    attempt: int = 1,
) -> ResponseIdentity:
    return ResponseIdentity(turn_id, presentation_epoch, round_no, attempt)


@pytest.mark.anyio
async def test_json_output_initializes_and_flushes_assistant_state() -> None:
    stdout = io.StringIO()
    state = JsonOutputState(_RecordWriter(), stdout)
    content = JsonContentSink(state)

    await content.emit(AssistantTextDelta("first ", _identity()))
    await content.emit(AssistantTextDelta("second", _identity()))

    assert stdout.getvalue() == ""
    await content.emit(AssistantSegmentCompleted(_identity()))

    event = json.loads(stdout.getvalue())
    assert event["item"]["text"] == "first second"
    assert {
        key: event["item"][key]
        for key in ("turn_id", "presentation_epoch", "round", "attempt")
    } == _identity().as_dict()


def test_json_output_omits_write_stdin_start_event() -> None:
    stdout = io.StringIO()
    state = JsonOutputState(_RecordWriter(), stdout)
    control = JsonOutputControl(state)

    control.record_tool_arguments(
        "write_stdin",
        {"session_id": "session-1", "stdin": "\n"},
        call_id="stdin-call",
    )

    assert stdout.getvalue() == ""


@pytest.mark.anyio
async def test_json_hook_startup_warning_is_codex_error_item() -> None:
    stdout = io.StringIO()
    state = JsonOutputState(_RecordWriter(), stdout)
    presentation = JsonPresentationSink(state)

    await presentation.emit(_run_started_view(hook_warnings=(
        "skipping MCP tool hook in config.toml: "
        "MCP invocation is not available yet",
    )))

    assert [json.loads(line) for line in stdout.getvalue().splitlines()] == [
        {"type": "thread.started", "thread_id": "thread_test"},
        {
            "type": "item.completed",
            "item": {
                "id": "item_0",
                "type": "error",
                "message": (
                    "skipping MCP tool hook in config.toml: "
                    "MCP invocation is not available yet"
                ),
            },
        },
        {"type": "turn.started"},
    ]


@pytest.mark.anyio
async def test_json_patch_output_preserves_raw_and_structured_facts() -> None:
    stdout = io.StringIO()
    state = JsonOutputState(_RecordWriter(), stdout)
    presentation = JsonPresentationSink(state)
    raw_patch = (
        "*** Begin Patch\n"
        "*** Add File: new.txt\n"
        "+new\n"
        "*** End Patch"
    )
    files = [{
        "path": "new.txt",
        "source_path": None,
        "action": "create",
        "added_lines": 1,
        "removed_lines": 0,
    }]

    await presentation.emit(build_native_tool_result_view(
        "apply_patch",
        {"patch": raw_patch},
        ok=True,
        data={
            "files": files,
            "delta": {
                "exact": True,
                "changes": [{
                    "path": "new.txt",
                    "source_path": None,
                        "action": "create",
                        "old_content": None,
                        "new_content": "new\n",
                        "hunks": [{"lines": [{
                            "kind": "add",
                            "text": "new",
                            "old_line": None,
                            "new_line": 1,
                        }]}],
                    }],
            },
        },
        cost_ms=12,
        call_id="patch-json",
    ))

    event = json.loads(stdout.getvalue())
    assert event["item"] == {
        "id": "patch-json",
        "type": "file_change",
        "status": "completed",
        "patch": raw_patch,
        "files": files,
        "error": None,
        "duration_ms": 12,
    }


@pytest.mark.anyio
async def test_json_output_preserves_structured_text_semantics() -> None:
    stdout = io.StringIO()
    state = JsonOutputState(_RecordWriter(), stdout)
    content = JsonContentSink(state)
    raw = "id\tdevice\x1b]52;c;payload\x1b\\"

    await content.emit(AssistantTextDelta(raw, _identity()))
    await content.emit(AssistantSegmentCompleted(_identity()))

    event = json.loads(stdout.getvalue())
    assert event["item"]["text"] == raw


@pytest.mark.anyio
async def test_json_output_distinguishes_response_retry_from_worker_takeover() -> None:
    """验证结构化输出保留 provider response 的精确替换作用域。"""
    stdout = io.StringIO()
    state = JsonOutputState(_RecordWriter(), stdout)
    content = JsonContentSink(state)

    await content.emit(AssistantResponseSuperseded(
        turn_id="turn_test",
        presentation_epoch=1,
        round=2,
        attempt=2,
    ))

    assert json.loads(stdout.getvalue()) == {
        "type": "response.superseded",
        "turn_id": "turn_test",
        "presentation_epoch": 1,
        "round": 2,
        "attempt": 2,
        "invalidated_item_ids": [],
    }


@pytest.mark.anyio
async def test_json_output_invalidates_exact_assistant_items() -> None:
    """验证机器消费者可按 item ID 精确排除旧 response。"""
    stdout = io.StringIO()
    state = JsonOutputState(_RecordWriter(), stdout)
    content = JsonContentSink(state)

    round_one = _identity(round_no=1)
    round_two_attempt_one = _identity(round_no=2)
    round_two_attempt_two = _identity(round_no=2, attempt=2)

    await content.emit(AssistantTextDelta("round one", round_one))
    await content.emit(AssistantSegmentCompleted(round_one))
    await content.emit(AssistantTextDelta("old partial", round_two_attempt_one))
    await content.emit(AssistantResponseSuperseded(
        turn_id="turn_test",
        presentation_epoch=1,
        round=2,
        attempt=2,
    ))
    await content.emit(AssistantTextDelta("new answer", round_two_attempt_two))
    await content.emit(AssistantSegmentCompleted(round_two_attempt_two))
    await content.emit(AssistantPresentationSuperseded(
        turn_id="turn_test",
        superseded_epoch=1,
        presentation_epoch=2,
    ))

    events = [json.loads(line) for line in stdout.getvalue().splitlines()]
    completed = [event for event in events if event["type"] == "item.completed"]
    response_marker = next(
        event for event in events if event["type"] == "response.superseded"
    )
    presentation_marker = next(
        event for event in events if event["type"] == "presentation.superseded"
    )

    assert [event["item"]["id"] for event in completed] == [
        "item_0", "item_1", "item_2"
    ]
    assert completed[0]["item"] == {
        "id": "item_0",
        "type": "agent_message",
        "text": "round one",
        **round_one.as_dict(),
    }
    assert response_marker["invalidated_item_ids"] == ["item_1"]
    assert presentation_marker["invalidated_item_ids"] == ["item_0", "item_2"]


@pytest.mark.anyio
async def test_json_output_rejects_mismatched_completion_identity() -> None:
    """验证完成边界不能错误关闭另一个 response 的正文。"""
    state = JsonOutputState(_RecordWriter(), io.StringIO())
    content = JsonContentSink(state)

    await content.emit(AssistantTextDelta("partial", _identity(attempt=1)))

    with pytest.raises(RuntimeError, match="completion identity"):
        await content.emit(AssistantSegmentCompleted(_identity(attempt=2)))


@pytest.mark.anyio
async def test_json_output_uses_final_text_and_deduplicates_completed_item() -> None:
    stdout = io.StringIO()
    state = JsonOutputState(_RecordWriter(), stdout)
    content = JsonContentSink(state)
    identity = _identity()

    await content.emit(AssistantTextDelta(
        "partial",
        identity,
        item_id="item-1",
    ))
    await content.emit(AssistantSegmentCompleted(
        identity,
        final_text="complete answer",
        item_id="item-1",
    ))
    await content.emit(AssistantTextDelta(
        "duplicate replay",
        identity,
        item_id="item-1",
    ))
    await content.emit(AssistantSegmentCompleted(
        identity,
        final_text="duplicate final",
        item_id="item-1",
    ))

    events = [json.loads(line) for line in stdout.getvalue().splitlines()]
    assert len(events) == 1
    assert events[0]["item"]["id"] == "item-1"
    assert events[0]["item"]["text"] == "complete answer"


@pytest.mark.anyio
async def test_json_output_preserves_empty_authoritative_final_text() -> None:
    stdout = io.StringIO()
    state = JsonOutputState(_RecordWriter(), stdout)
    content = JsonContentSink(state)
    identity = _identity()

    await content.emit(AssistantTextDelta(
        "partial",
        identity,
        item_id="item-empty",
    ))
    await content.emit(AssistantSegmentCompleted(
        identity,
        final_text="",
        item_id="item-empty",
    ))

    events = [json.loads(line) for line in stdout.getvalue().splitlines()]
    assert events[0]["item"]["text"] == ""


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
async def test_json_output_ignores_hook_lifecycle_like_codex_exec() -> None:
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
async def test_json_hook_lifecycle_does_not_reserve_item_ids() -> None:
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

    assert stdout.getvalue() == ""
    assert state.item_id() == "item_1"
