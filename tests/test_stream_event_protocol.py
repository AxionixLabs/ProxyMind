# -*- coding: utf-8 -*-

import pytest

from mind_nova.stream_events import (
    TextMetaEvent,
    ToolApprovalRequiredEvent,
    ToolCallEvent,
    ToolOutputEvent,
    TurnDoneEvent,
    TurnInputAcceptedEvent,
    TurnLogicalSettledEvent,
    UnknownStreamEvent,
    parse_stream_event,
)


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
    event = parse_stream_event({
        "type": "tool.call",
        "tool": "shell_command",
        "call_id": "call-1",
        "arguments": {"command": "pytest -q"},
        "meta": {"domain": "coding"},
        "execution": {"target": "client"},
        "approvalRequired": "required",
    })

    assert isinstance(event, ToolCallEvent)
    assert event.name == "shell_command"
    assert event.call_id == "call-1"
    assert event.arguments == {"command": "pytest -q"}
    assert event.meta == {"domain": "coding"}
    assert event.execution == {"target": "client"}
    assert event.approval_required is True


def test_tool_call_event_parses_false_boolean_text() -> None:
    event = parse_stream_event({
        "type": "tool.call",
        "approved": "false",
    })

    assert isinstance(event, ToolCallEvent)
    assert event.approved is False


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
        "result": {"ok": True, "text": "done"},
    })

    assert isinstance(approval, ToolApprovalRequiredEvent)
    assert approval.approval == {"id": "approval-1"}
    assert isinstance(output, ToolOutputEvent)
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


@pytest.mark.parametrize("payload", ({}, {"type": ""}))
def test_stream_event_requires_type(payload) -> None:
    with pytest.raises(ValueError, match="type is required"):
        parse_stream_event(payload)


def test_stream_event_requires_object() -> None:
    with pytest.raises(TypeError, match="must be an object"):
        parse_stream_event([])
