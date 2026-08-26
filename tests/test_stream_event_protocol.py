# -*- coding: utf-8 -*-

import pytest

from mind_nova.stream_events import (
    PresentationSupersededEvent,
    TextMetaEvent,
    ToolApprovalRequiredEvent,
    ToolCallEvent,
    ToolOutputEvent,
    TurnDoneEvent,
    TurnFailedEvent,
    TurnRetryingEvent,
    TurnInputAcceptedEvent,
    TurnLogicalSettledEvent,
    TurnReconciliationRequiredEvent,
    UnknownStreamEvent,
    parse_stream_event as _parse_stream_event,
)


def parse_stream_event(payload):
    """为协议单测补齐当前 wire envelope。"""
    current = dict(payload)
    if str(current.get("type") or "") != "ping":
        current.setdefault("proto", "mind.chat")
        current.setdefault("cid", "cid_test")
        current.setdefault("sid", "sid_test")
        current.setdefault("turn_id", "turn_test")
        current.setdefault("event_seq", 1)
        current.setdefault("presentation_epoch", 1)
        if str(current.get("type") or "").startswith("text."):
            current.setdefault("segment_id", "segment_test")
    return _parse_stream_event(current)


def _durable_tool_event() -> dict:
    """构造最新客户端工具调用协议事件。"""
    return {
        "proto": "mind.chat",
        "cid": "cid_test",
        "sid": "sid_test",
        "type": "tool.call",
        "turn_id": "turn_test",
        "event_seq": 1,
        "presentation_epoch": 2,
        "name": "shell_command",
        "call_id": "call_test",
        "arguments": {"command": "echo ready"},
        "reason": "模型需要检查命令输出。",
    }


def test_latest_tool_protocol_parses_direct_tool_fields() -> None:
    event = parse_stream_event(_durable_tool_event())

    assert isinstance(event, ToolCallEvent)
    assert event.name == "shell_command"
    assert event.call_id == "call_test"
    assert event.arguments == {"command": "echo ready"}
    assert event.reason == "模型需要检查命令输出。"


def test_presentation_superseded_requires_preceding_epoch() -> None:
    event = parse_stream_event({
        "proto": "mind.chat",
        "type": "presentation.superseded",
        "turn_id": "turn_test",
        "presentation_epoch": 2,
        "superseded_epoch": 1,
    })
    assert isinstance(event, PresentationSupersededEvent)

    with pytest.raises(ValueError, match="must precede"):
        parse_stream_event({
            "proto": "mind.chat",
            "type": "presentation.superseded",
            "turn_id": "turn_test",
            "presentation_epoch": 2,
            "superseded_epoch": 2,
        })


def test_turn_retrying_requires_strict_attempt_metadata() -> None:
    """校验 provider 重试事件的类型化字段和边界。"""
    event = parse_stream_event({
        "type": "turn.retrying",
        "turn_id": "turn_test",
        "event_seq": 7,
        "round": 2,
        "attempt": 2,
        "max_attempts": 3,
        "retry_in_ms": 250,
        "reason": "stream_reset",
    })

    assert isinstance(event, TurnRetryingEvent)
    assert event.attempt == 2
    assert event.max_attempts == 3
    assert event.retry_in_ms == 250

    for invalid in (
        {"attempt": 0, "max_attempts": 3, "retry_in_ms": 0},
        {"attempt": 4, "max_attempts": 3, "retry_in_ms": 0},
        {"attempt": 2, "max_attempts": 3, "retry_in_ms": -1},
        {"attempt": 2, "max_attempts": True, "retry_in_ms": 0},
        {"attempt": 2, "max_attempts": 3, "retry_in_ms": True},
    ):
        with pytest.raises(ValueError):
            parse_stream_event({"type": "turn.retrying", "round": 2, **invalid})

    with pytest.raises(ValueError, match="round"):
        parse_stream_event({
            "type": "turn.retrying",
            "attempt": 2,
            "max_attempts": 3,
            "retry_in_ms": 0,
        })


def test_text_meta_event_copies_structured_metadata() -> None:
    sources = [{"url": "https://example.com"}]
    payload = {
        "type": "text.meta",
        "segment_id": "segment-1",
        "annotations": [{"start": 0}],
        "citations": [{"source": 0}],
        "sources": sources,
        "source_count": 1,
        "proto": "mind.chat",
        "round": 2,
    }

    event = parse_stream_event(payload)
    sources[0]["url"] = "changed"

    assert isinstance(event, TextMetaEvent)
    assert event.segment_id == "segment-1"
    assert event.sources == ({"url": "https://example.com"},)
    assert event.source_count == 1
    assert event.proto == "mind.chat"
    assert event.round == 2


def test_tool_call_event_normalizes_wire_aliases() -> None:
    payload = _durable_tool_event()
    payload.pop("name")
    payload.update({
        "tool": "shell_command",
        "call_id": "call-1",
        "arguments": {"command": "pytest -q"},
        "reason": "模型需要运行测试。",
    })
    event = parse_stream_event(payload)

    assert isinstance(event, ToolCallEvent)
    assert event.name == "shell_command"
    assert event.call_id == "call-1"
    assert event.arguments == {"command": "pytest -q"}
    assert event.reason == "模型需要运行测试。"


def test_tool_call_event_accepts_calls_without_execution_metadata() -> None:
    event = parse_stream_event({
        "type": "tool.call",
        "name": "shell_command",
        "call_id": "call_without_execution",
        "arguments": {"command": "pwd"},
        "reason": "模型需要确认工作目录。",
    })

    assert isinstance(event, ToolCallEvent)
    assert event.reason == "模型需要确认工作目录。"


def test_shell_tool_call_requires_model_reason() -> None:
    with pytest.raises(ValueError, match="shell reason is required"):
        parse_stream_event({
            "type": "tool.call",
            "name": "shell_command",
            "call_id": "call_without_reason",
            "arguments": {"command": "pwd"},
        })


def test_turn_reconciliation_required_is_a_typed_terminal_pause() -> None:
    event = parse_stream_event({
        "proto": "mind.chat",
        "type": "turn.reconciliation_required",
        "turn_id": "turn_test",
        "presentation_epoch": 2,
        "status": "reconciliation_required",
        "effect_id": "effect_test",
        "error": "provider succeeded but commit failed",
    })

    assert isinstance(event, TurnReconciliationRequiredEvent)
    assert event.effect_id == "effect_test"
    assert event.status == "reconciliation_required"


def test_stream_event_rejects_fractional_integer_fields() -> None:
    event = parse_stream_event({
        "type": "text.meta",
        "round": 1.5,
        "source_count": 2.5,
    })

    assert isinstance(event, TextMetaEvent)
    assert event.round is None
    assert event.source_count is None


def test_unknown_event_preserves_extension_payload_and_display() -> None:
    event = parse_stream_event({
        "type": "provider.progress",
        "display": {"text": "Loading"},
        "provider_data": {"step": 2},
    })

    assert isinstance(event, UnknownStreamEvent)
    assert event.display == {"text": "Loading"}
    assert event.payload["provider_data"] == {"step": 2}


def test_stream_event_preserves_turn_identity_and_event_sequence() -> None:
    event = parse_stream_event({
        "type": "text.delta",
        "turn_id": "turn_1",
        "event_seq": 41,
        "text": "answer",
    })

    assert event.turn_id == "turn_1"
    assert event.cid == "cid_test"
    assert event.sid == "sid_test"
    assert event.event_seq == 41


@pytest.mark.parametrize("event_type", ("text.delta", "text.done", "text.meta"))
@pytest.mark.parametrize("segment_id", (None, "", "   "))
def test_text_events_require_stable_item_identity(event_type, segment_id) -> None:
    payload = {
        "type": event_type,
        "segment_id": segment_id,
        "text": "answer",
    }

    with pytest.raises(ValueError, match=f"{event_type} segment_id"):
        _parse_stream_event({
            **payload,
            "proto": "mind.chat",
            "cid": "cid_test",
            "sid": "sid_test",
            "turn_id": "turn_test",
            "event_seq": 1,
            "presentation_epoch": 1,
        })


def test_text_event_segment_id_is_exposed_as_item_id() -> None:
    event = parse_stream_event({
        "type": "text.delta",
        "segment_id": "item-1",
        "text": "answer",
    })

    assert event.item_id == "item-1"


def test_text_done_preserves_authoritative_final_text() -> None:
    event = parse_stream_event({
        "type": "text.done",
        "segment_id": "item-1",
        "final_text": "complete answer",
    })

    assert event.final_text == "complete answer"


def test_text_done_preserves_authoritative_whitespace_and_empty_text() -> None:
    whitespace = parse_stream_event({
        "type": "text.done",
        "segment_id": "item-1",
        "final_text": "  complete\n",
    })
    empty = parse_stream_event({
        "type": "text.done",
        "segment_id": "item-2",
        "final_text": "",
    })

    assert whitespace.final_text == "  complete\n"
    assert empty.final_text == ""


def test_retrying_can_name_the_item_it_supersedes() -> None:
    event = parse_stream_event({
        "type": "turn.retrying",
        "round": 1,
        "attempt": 2,
        "max_attempts": 3,
        "retry_in_ms": 0,
        "supersedes_item_id": "item-old",
    })

    assert event.supersedes_item_id == "item-old"


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("proto", "stream.v2"),
        ("cid", ""),
        ("sid", ""),
        ("turn_id", ""),
        ("event_seq", 0),
        ("presentation_epoch", 0),
    ),
)
def test_stream_event_rejects_invalid_current_envelope(field, value) -> None:
    payload = {
        "proto": "mind.chat",
        "cid": "cid_test",
        "sid": "sid_test",
        "turn_id": "turn_test",
        "event_seq": 1,
        "presentation_epoch": 1,
        "type": "text.delta",
        "text": "answer",
    }
    payload[field] = value

    with pytest.raises(ValueError):
        _parse_stream_event(payload)


@pytest.mark.parametrize("field", ("seq", "replace_current_response"))
def test_stream_event_rejects_removed_fields(field) -> None:
    payload = _durable_tool_event()
    payload[field] = 1

    with pytest.raises(ValueError, match="removed protocol field"):
        _parse_stream_event(payload)


@pytest.mark.parametrize("event_seq", [0, -1, True, "41"])
def test_stream_event_rejects_invalid_explicit_event_sequence(event_seq) -> None:
    with pytest.raises(ValueError, match="event_seq"):
        parse_stream_event({
            "type": "text.delta",
            "event_seq": event_seq,
            "text": "answer",
        })


def test_tool_approval_and_output_events_copy_payloads() -> None:
    approval = parse_stream_event({
        "type": "tool.approval_required",
        "proto": "mind.chat",
        "cid": "conversation-1",
        "sid": "session-1",
        "turn_id": "turn-1",
        "event_seq": 1,
        "call_id": "call-1",
        "approval_id": "approval-1",
        "kind": "command",
        "environmentId": "workspace-write",
        "started_at_ms": 42,
        "plugin_id": "plugin-1",
        "script_path": "scripts/check.ps1",
        "command": ["pwsh", "-Command", "Get-Date"],
        "cwd": ".",
        "cwd_raw": "C:/workspace",
        "reason": "模型需要运行测试。",
        "available_decisions": ["accept", "decline"],
    })
    output = parse_stream_event({
        "type": "tool.output",
        "name": "remote_tool",
        "call_id": "call-2",
        "status": "completed",
        "result": {"ok": True, "text": "done"},
    })

    assert isinstance(approval, ToolApprovalRequiredEvent)
    assert approval.approval_id == "approval-1"
    assert approval.call_id == "call-1"
    assert approval.turn_id == "turn-1"
    assert approval.kind == "command"
    assert approval.environment_id == "workspace-write"
    assert approval.started_at_ms == 42
    assert approval.plugin_id == "plugin-1"
    assert approval.script_path == "scripts/check.ps1"
    assert approval.command == ["pwsh", "-Command", "Get-Date"]
    assert approval.cwd == "."
    assert approval.cwd_raw == "C:/workspace"
    assert approval.reason == "模型需要运行测试。"
    assert approval.available_decisions == ("accept", "decline")
    assert isinstance(output, ToolOutputEvent)
    assert output.payload["status"] == "completed"
    assert output.payload["result"] == {"ok": True, "text": "done"}


def test_tool_approval_allows_missing_optional_reason() -> None:
    approval = parse_stream_event({
        "type": "tool.approval_required",
        "proto": "mind.chat",
        "cid": "conversation-1",
        "sid": "session-1",
        "turn_id": "turn-1",
        "event_seq": 1,
        "call_id": "call-1",
        "kind": "command",
        "command": ["git", "status"],
        "cwd": ".",
        "available_decisions": ["accept", "decline"],
    })

    assert isinstance(approval, ToolApprovalRequiredEvent)
    assert approval.reason == ""


def test_tool_approval_requires_available_decisions() -> None:
    with pytest.raises(ValueError, match="available_decisions"):
        parse_stream_event({
            "type": "tool.approval_required",
            "call_id": "call-1",
            "approval_id": "approval-1",
            "kind": "command",
            "command": "pytest -q",
            "cwd": ".",
            "reason": "模型需要运行测试。",
        })


def test_turn_control_events_preserve_stable_input_identity() -> None:
    accepted = parse_stream_event({
        "type": "turn.input.accepted",
        "turn_id": "turn_1",
        "client_message_id": "message_1",
    })
    settled = parse_stream_event({
        "type": "turn.logical_settled",
        "turn_id": "turn_1",
        "next_input": {
            "client_message_id": "message_2",
            "text": "continue with this",
            "attachments": [{"kind": "image"}],
            "extras": {"source": "steer"},
        },
    })
    done = parse_stream_event({
        "type": "turn.done",
        "turn_id": "turn_1",
        "status": "interrupted",
    })

    assert isinstance(accepted, TurnInputAcceptedEvent)
    assert accepted.turn_id == "turn_1"
    assert accepted.client_message_id == "message_1"
    assert isinstance(settled, TurnLogicalSettledEvent)
    assert settled.next_input is not None
    assert settled.next_input.client_message_id == "message_2"
    assert settled.next_input.attachments == ({"kind": "image"},)
    assert isinstance(done, TurnDoneEvent)
    assert done.status == "interrupted"


def test_turn_terminal_events_preserve_response_metadata() -> None:
    usage = {
        "input_tokens": 11,
        "output_tokens": 7,
        "cache": {"read_tokens": 3},
    }
    done = parse_stream_event({
        "type": "turn.done",
        "status": "incomplete",
        "reason": "max_output_tokens",
        "can_continue": True,
        "response_id": "msg_1",
        "model": "claude-test",
        "route": "messages",
        "request_id": "req_1",
        "service_tier": "standard",
        "usage": usage,
        "stop_reason": "max_tokens",
        "stop_sequence": None,
    })
    failed = parse_stream_event({
        "type": "turn.failed",
        "status": "failed",
        "error": {"message": "pause_turn is not supported"},
        "route": "messages",
        "usage": {"output_tokens": 2},
        "stop_reason": "pause_turn",
    })
    usage["cache"]["read_tokens"] = 99

    assert isinstance(done, TurnDoneEvent)
    assert done.status == "incomplete"
    assert done.reason == "max_output_tokens"
    assert done.can_continue is True
    assert done.response_id == "msg_1"
    assert done.model == "claude-test"
    assert done.route == "messages"
    assert done.request_id == "req_1"
    assert done.service_tier == "standard"
    assert done.usage["cache"] == {"read_tokens": 3}
    assert done.stop_reason == "max_tokens"
    assert done.stop_sequence is None
    assert isinstance(failed, TurnFailedEvent)
    assert failed.status == "failed"
    assert failed.error == "pause_turn is not supported"
    assert failed.stop_reason == "pause_turn"
    assert failed.usage == {"output_tokens": 2}


def test_logical_settlement_accepts_null_next_input() -> None:
    settled = parse_stream_event({
        "type": "turn.logical_settled",
        "turn_id": "turn_1",
        "next_input": None,
    })

    assert isinstance(settled, TurnLogicalSettledEvent)
    assert settled.next_input is None


@pytest.mark.parametrize("payload", ({}, {"type": ""}))
def test_stream_event_requires_type(payload) -> None:
    with pytest.raises(ValueError, match="type is required"):
        parse_stream_event(payload)


def test_stream_event_requires_object() -> None:
    with pytest.raises(TypeError, match="must be an object"):
        _parse_stream_event([])
