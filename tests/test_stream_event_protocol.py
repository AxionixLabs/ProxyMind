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
    TurnInputAcceptedEvent,
    TurnLogicalSettledEvent,
    TurnReconciliationRequiredEvent,
    UnknownStreamEvent,
    parse_stream_event,
)


def _durable_tool_event() -> dict:
    """构造最新客户端工具执行协议事件。"""
    return {
        "proto": "mind.chat",
        "type": "tool.call",
        "turn_id": "turn_test",
        "presentation_epoch": 2,
        "name": "shell_command",
        "call_id": "call_test",
        "arguments": {"command": "echo ready"},
        "execution": {
            "target": "local",
            "effect": {
                "effect_id": "effect_test",
                "fingerprint": "a" * 64,
                "class": "non_replayable",
                "replay_policy": "manual",
                "provider_idempotency_key": "",
                "status": "dispatching",
                "dispatch_required": True,
                "dispatch_count": 1,
            },
        },
        "checkpoint": {
            "checkpoint_id": "checkpoint_test",
            "required": True,
            "workspace": {"root": "/tmp/workspace"},
            "artifact": {},
        },
    }


def test_latest_tool_protocol_parses_effect_and_checkpoint_strictly() -> None:
    event = parse_stream_event(_durable_tool_event())

    assert isinstance(event, ToolCallEvent)
    assert event.effect is not None
    assert event.effect.replay_policy == "manual"
    assert event.checkpoint is not None
    assert event.checkpoint.checkpoint_id == "checkpoint_test"


@pytest.mark.parametrize("missing", ("effect", "checkpoint"))
def test_latest_tool_protocol_rejects_missing_durability_gate(missing: str) -> None:
    payload = _durable_tool_event()
    if missing == "effect":
        payload["execution"].pop("effect")
    else:
        payload.pop("checkpoint")

    with pytest.raises(ValueError, match=missing):
        parse_stream_event(payload)


@pytest.mark.parametrize("required", (False, 1, "true", None))
def test_latest_tool_protocol_requires_literal_checkpoint_true(required) -> None:
    payload = _durable_tool_event()
    payload["checkpoint"]["required"] = required

    with pytest.raises(ValueError, match="required must be true"):
        parse_stream_event(payload)


@pytest.mark.parametrize(
    ("updates", "message"),
    [
        ({"class": "idempotent", "replay_policy": "provider_idempotent",
          "provider_idempotency_key": "provider-key"}, "manual non-replayable"),
        ({"status": "prepared"}, "not dispatchable"),
        ({"dispatch_required": False}, "not dispatchable"),
        ({"dispatch_count": 0}, "not dispatchable"),
    ],
)
def test_latest_tool_protocol_rejects_inconsistent_effect_semantics(
    updates: dict,
    message: str,
) -> None:
    payload = _durable_tool_event()
    payload["execution"]["effect"].update(updates)

    with pytest.raises(ValueError, match=message):
        parse_stream_event(payload)


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


def test_text_meta_event_copies_structured_metadata() -> None:
    sources = [{"url": "https://example.com"}]
    payload = {
        "type": "text.meta",
        "segment_id": "segment-1",
        "annotations": [{"start": 0}],
        "citations": [{"source": 0}],
        "sources": sources,
        "source_count": 1,
        "proto": "stream.v2",
        "round": 2,
    }

    event = parse_stream_event(payload)
    sources[0]["url"] = "changed"

    assert isinstance(event, TextMetaEvent)
    assert event.segment_id == "segment-1"
    assert event.sources == ({"url": "https://example.com"},)
    assert event.source_count == 1
    assert event.proto == "stream.v2"
    assert event.round == 2


def test_tool_call_event_normalizes_wire_aliases() -> None:
    payload = _durable_tool_event()
    payload.pop("name")
    payload.update({
        "tool": "shell_command",
        "call_id": "call-1",
        "arguments": {"command": "pytest -q"},
        "meta": {"domain": "coding"},
        "approvalRequired": "required",
    })
    event = parse_stream_event(payload)

    assert isinstance(event, ToolCallEvent)
    assert event.name == "shell_command"
    assert event.call_id == "call-1"
    assert event.arguments == {"command": "pytest -q"}
    assert event.meta == {"domain": "coding"}
    assert event.execution is not None
    assert event.execution["target"] == "local"
    assert event.approval_required is True


def test_tool_call_event_parses_false_boolean_text() -> None:
    payload = _durable_tool_event()
    payload["approved"] = "false"
    event = parse_stream_event(payload)

    assert isinstance(event, ToolCallEvent)
    assert event.approved is False


def test_all_tool_call_protocols_require_current_durability_fields() -> None:
    with pytest.raises(ValueError, match="execution.effect"):
        parse_stream_event({
            "type": "tool.call",
            "name": "shell_command",
            "call_id": "call_legacy",
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
    assert event.event_seq == 41


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
        "name": "shell_command",
        "call_id": "call-1",
        "approval": {"id": "approval-1"},
    })
    output = parse_stream_event({
        "type": "tool.output",
        "name": "remote_tool",
        "call_id": "call-2",
        "status": "completed",
        "result": {"ok": True, "text": "done"},
    })

    assert isinstance(approval, ToolApprovalRequiredEvent)
    assert approval.approval == {"id": "approval-1"}
    assert isinstance(output, ToolOutputEvent)
    assert output.payload["status"] == "completed"
    assert output.payload["result"] == {"ok": True, "text": "done"}


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
        parse_stream_event([])
